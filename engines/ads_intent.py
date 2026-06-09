"""
BAD DECISION AI — Engine 1: Digital Ads Intelligence (Scrapling-First + DeepSeek Fallback)
=========================================================================================
This engine scrapes Meta Ads Library and Google search to find
businesses that are actively running ads.

PIPELINE:
1. Scrapling fetches REAL data from ad libraries and search engines
2. DeepSeek structures the scraped HTML/text into clean lead objects
3. Multi-source enrichment for contact details (website → contact pages → web search)
4. Validation gates run (DNS → Footprint → SMTP based on tier)
5. Dedup & cache
"""

import json
from typing import List, Dict, Any

from scraping.stealth_fetcher import (
    stealth_fetch,
    stealth_fetch_contact_page,
    extract_text_from_html,
    build_meta_ads_library_url,
    build_google_search_url,
    build_bing_search_url,
    build_duckduckgo_search_url,
    build_contact_search_url,
)
from ai.deepseek_middleware import execute_llm_payload, DEEPSEEK_SCOUT_MODEL
from validation.gate_dns import check_dns
from validation.gate_footprint import check_footprint
from validation.gate_smtp import check_smtp
from dedup.hash_dedup import compute_hash, check_duplicate


MAX_LEADS = 50


async def run_ads_intent(
    query: str,
    user_tier: str = "free",
) -> List[Dict[str, Any]]:
    """Search for companies running ads based on the user's query."""

    leads = []

    # --------------------------------------------------------
    # PHASE 1: SCRAPLING — Fetch real data from the web
    # --------------------------------------------------------
    print(f"[ADS_INTENT] Scrapling Phase: Fetching ad data for '{query}'")

    scraped_texts = []

    # Source 1: Meta Ads Library
    meta_url = build_meta_ads_library_url(query)
    meta_result = await stealth_fetch(meta_url)
    if meta_result:
        text = extract_text_from_html(meta_result["html"])
        if text:
            scraped_texts.append({"source": "Meta Ads Library", "content": text})
            print(f"[ADS_INTENT] Scraped Meta Ads Library: {len(text)} chars")
    else:
        print(f"[ADS_INTENT] Meta Ads Library fetch failed — continuing with other sources")

    # Source 2: Google search
    google_url = build_google_search_url(f"{query} running ads advertising")
    google_result = await stealth_fetch(google_url)
    if google_result:
        text = extract_text_from_html(google_result["html"])
        if text:
            scraped_texts.append({"source": "Google Search", "content": text})
            print(f"[ADS_INTENT] Scraped Google Search: {len(text)} chars")
    else:
        print(f"[ADS_INTENT] Google Search fetch failed")

    # Source 3: Bing search
    bing_url = build_bing_search_url(f"{query} running ads advertising")
    bing_result = await stealth_fetch(bing_url)
    if bing_result:
        text = extract_text_from_html(bing_result["html"])
        if text:
            scraped_texts.append({"source": "Bing Search", "content": text})
            print(f"[ADS_INTENT] Scraped Bing Search: {len(text)} chars")
    else:
        print(f"[ADS_INTENT] Bing Search fetch failed")

    # Source 4: DuckDuckGo HTML
    ddg_url = build_duckduckgo_search_url(f"{query} running ads advertising business")
    ddg_result = await stealth_fetch(ddg_url)
    if ddg_result:
        text = extract_text_from_html(ddg_result["html"])
        if text:
            scraped_texts.append({"source": "DuckDuckGo", "content": text})
            print(f"[ADS_INTENT] Scraped DuckDuckGo: {len(text)} chars")
    else:
        print(f"[ADS_INTENT] DuckDuckGo fetch failed")

    # Combine all scraped content
    combined_text = ""
    if scraped_texts:
        combined_text = "\n\n".join(
            f"--- SOURCE: {s['source']} ---\n{s['content']}"
            for s in scraped_texts
        )
        print(f"[ADS_INTENT] Combined scraped text: {len(combined_text)} chars from {len(scraped_texts)} sources")
    else:
        print(f"[ADS_INTENT] All Scrapling sources returned empty text — using DeepSeek knowledge fallback")

    # --------------------------------------------------------
    # PHASE 2: DEEPSEEK — Structure the data
    # --------------------------------------------------------
    print(f"[ADS_INTENT] DeepSeek Phase: Structuring data")

    if combined_text:
        structure_prompt = f"""
        You are a business data extractor. Below is REAL TEXT scraped from the internet
        about businesses related to: "{query}"

        Your job is to extract REAL businesses mentioned in this text.
        Do NOT invent or hallucinate businesses that are not in the text.

        SCRAPED CONTENT:
        {combined_text[:12000]}

        For each REAL business you find, provide:
        - company_name: The exact business name as mentioned
        - website_url: Their website domain if mentioned (or "ABSENT" if not found)
        - ad_platform: Which platform they advertise on if mentioned (or "ABSENT")

        Return a JSON object with a "businesses" array. Find up to {MAX_LEADS} businesses.
        If you cannot find data for a field, write "ABSENT".
        """
    else:
        structure_prompt = f"""
        You are a business data researcher. Find real businesses related to: "{query}"
        that are likely running digital ads (Google Ads, Meta Ads, etc.).

        Use your knowledge to identify businesses that would match this search.
        Include as many REAL businesses as you can — aim for {MAX_LEADS}.

        For each business you find, provide:
        - company_name: The real business name
        - website_url: Their website URL (or "ABSENT" if unknown)
        - ad_platform: Which platform they likely advertise on (or "ABSENT")

        Return a JSON object with a "businesses" array. Find up to {MAX_LEADS} businesses.
        If you cannot find data for a field, write "ABSENT".
        """

    try:
        response = await execute_llm_payload({
            "model": DEEPSEEK_SCOUT_MODEL,
            "messages": [
                {"role": "system", "content": "You are a precise data extractor. Only extract REAL businesses. Never invent data. Always respond with valid JSON. Use 'ABSENT' for missing data. Return as many real businesses as you can find."},
                {"role": "user", "content": structure_prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        })

        content = response.get("choices", [{}])[0].get("message", {}).get("content", "{}")

        try:
            parsed = json.loads(content)
            businesses = parsed.get("businesses", parsed.get("results", []))
            if isinstance(parsed, list):
                businesses = parsed
        except json.JSONDecodeError:
            businesses = []

    except Exception as e:
        print(f"[ADS_INTENT] DeepSeek structuring error: {e}")
        businesses = []

    print(f"[ADS_INTENT] DeepSeek extracted {len(businesses)} candidate businesses")

    # --------------------------------------------------------
    # PHASE 3: VALIDATE & ENRICH each business
    # --------------------------------------------------------
    for biz in businesses[:MAX_LEADS]:
        company_name = biz.get("company_name", "ABSENT")
        website_url = biz.get("website_url", "ABSENT")
        ad_platform = biz.get("ad_platform", "ABSENT")

        if company_name == "ABSENT" or not company_name:
            continue

        url_to_hash = website_url if website_url != "ABSENT" else company_name
        domain_hash = compute_hash(url_to_hash)

        is_dup, cached_data = await check_duplicate(domain_hash)
        if is_dup and cached_data:
            leads.append(cached_data)
            continue

        # Gate 1: DNS Check
        if website_url != "ABSENT":
            dns_ok = await check_dns(website_url)
            if not dns_ok:
                print(f"[ADS_INTENT] DNS failed for {website_url} — DROPPED")
                continue

        # Multi-source enrichment
        enrichment = await _enrich_lead(company_name, website_url, user_tier)

        lead = {
            "domain_hash": domain_hash,
            "company_name": company_name,
            "website_url": website_url,
            "dm_name": enrichment.get("dm_name", "ABSENT"),
            "dm_position": enrichment.get("dm_position", "ABSENT"),
            "verified_email": enrichment.get("verified_email", "ABSENT"),
            "is_catchall": False,
            "linkedin": enrichment.get("linkedin", "ABSENT"),
            "instagram": enrichment.get("instagram", "ABSENT"),
            "phone": enrichment.get("phone", "ABSENT"),
            "ad_platform": ad_platform,
        }

        # Gate 2: Footprint (Starter+)
        if user_tier in ("starter", "growth", "pro"):
            footprint_ok = check_footprint(lead)
            if not footprint_ok:
                print(f"[ADS_INTENT] Footprint failed for {company_name} — DROPPED")
                continue

        # Gate 3: SMTP (Pro only)
        if user_tier == "pro" and lead["verified_email"] != "ABSENT":
            smtp_ok, is_catchall = await check_smtp(lead["verified_email"])
            lead["is_catchall"] = is_catchall
            if not smtp_ok and not is_catchall:
                print(f"[ADS_INTENT] SMTP failed for {lead['verified_email']} — DROPPED")
                continue

        leads.append(lead)

    return leads


async def _enrich_lead(
    company_name: str,
    website_url: str,
    user_tier: str,
) -> Dict[str, str]:
    """
    Multi-source enrichment for contact details:
    1. Try scraping company homepage
    2. If homepage fails → try /contact and /about pages
    3. If website scraping fails → DuckDuckGo search for contact details
    4. DeepSeek extracts structured info from whichever source succeeds
    """
    scraped_text = ""
    source_description = ""

    # STRATEGY 1: Try scraping the homepage
    if website_url and website_url != "ABSENT":
        print(f"[ADS_INTENT] Scrapling: Fetching company website {website_url}")
        site_result = await stealth_fetch(website_url)
        if site_result:
            scraped_text = extract_text_from_html(site_result["html"], max_chars=8000)
            if scraped_text:
                print(f"[ADS_INTENT] Scraped company website: {len(scraped_text)} chars")
                source_description = f"the website of {company_name} ({website_url})"

    # STRATEGY 2: Try contact/about pages
    if not scraped_text and website_url and website_url != "ABSENT":
        print(f"[ADS_INTENT] Homepage empty — trying contact/about pages for {website_url}")
        contact_result = await stealth_fetch_contact_page(website_url)
        if contact_result:
            scraped_text = extract_text_from_html(contact_result["html"], max_chars=8000)
            if scraped_text:
                print(f"[ADS_INTENT] Scraped contact page: {len(scraped_text)} chars")
                source_description = f"the contact/about page of {company_name} ({website_url})"

    # STRATEGY 3: Web search for contact details
    if not scraped_text:
        print(f"[ADS_INTENT] Website scraping failed — searching for contact details of {company_name}")
        search_url = build_contact_search_url(company_name)
        search_result = await stealth_fetch(search_url)
        if search_result:
            scraped_text = extract_text_from_html(search_result["html"], max_chars=8000)
            if scraped_text:
                print(f"[ADS_INTENT] Found contact info via web search: {len(scraped_text)} chars")
                source_description = f"web search results for '{company_name}' contact details"

    if scraped_text:
        prompt = f"""
        You are a business contact researcher. Below is REAL TEXT from {source_description}
        for the business: "{company_name}"

        Extract the following contact information from this text:
        - dm_name: Full name of the CEO, founder, or owner
        - dm_position: Their job title
        - verified_email: Their work email address (only if clearly in the text)
        - linkedin: Their LinkedIn profile URL
        - instagram: The company's Instagram URL
        - phone: The company phone number

        IMPORTANT RULES:
        - Only extract information clearly present in the text
        - Do NOT guess or generate email addresses using patterns
        - If you cannot find any piece of information, write "ABSENT"

        SOURCE TEXT:
        {scraped_text[:8000]}

        Return a single JSON object.
        """
    else:
        prompt = f"""
        You are a business contact researcher. Find verified contact details for:
        "{company_name}" ({website_url})

        Look for:
        - dm_name: Full name of the CEO, founder, or owner
        - dm_position: Their job title
        - verified_email: Their actual work email (only if CERTAIN — do NOT guess)
        - linkedin: Their LinkedIn profile URL (only if CERTAIN)
        - instagram: The company's Instagram URL
        - phone: The company phone number (only if CERTAIN)

        CRITICAL: Only provide information you are CONFIDENT is accurate.
        Do NOT generate email addresses using common patterns like info@, hello@, contact@.
        It is better to return "ABSENT" than to provide wrong information.

        Return a single JSON object.
        """

    try:
        response = await execute_llm_payload({
            "model": DEEPSEEK_SCOUT_MODEL,
            "messages": [
                {"role": "system", "content": "You are a precise data extractor. Only extract REAL, VERIFIED contact information. Never invent or guess data. Always respond with valid JSON. Use 'ABSENT' for missing data. It is better to return ABSENT than to provide wrong information."},
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        })

        content = response.get("choices", [{}])[0].get("message", {}).get("content", "{}")
        return json.loads(content)

    except Exception as e:
        print(f"[ADS_INTENT] Enrichment error for {company_name}: {e}")
        return {
            "dm_name": "ABSENT",
            "dm_position": "ABSENT",
            "verified_email": "ABSENT",
            "linkedin": "ABSENT",
            "instagram": "ABSENT",
            "phone": "ABSENT",
        }
