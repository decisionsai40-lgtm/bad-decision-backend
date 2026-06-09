"""
BAD DECISION AI — Engine 3: Web-Absent Aggregators (Scrapling-First + DeepSeek Fallback)
=========================================================================================
This engine finds businesses that exist ONLY on aggregator sites
(Yelp, Houzz, Zillow, Etsy, Amazon Storefronts) and do NOT
have their own website.

PIPELINE:
1. Scrapling fetches REAL data from Yelp, Houzz, and Google search
2. DeepSeek structures the scraped HTML/text into clean lead objects
3. HARD FILTER: Drop any business that HAS an external website link
4. Multi-source enrichment (aggregator profile → web search → DeepSeek knowledge)
5. Validation gates run (Footprint → SMTP based on tier, NO DNS gate)
6. Dedup & cache
"""

import json
from urllib.parse import urlparse
from typing import List, Dict, Any

from scraping.stealth_fetcher import (
    stealth_fetch,
    extract_text_from_html,
    extract_links_from_html,
    build_yelp_search_url,
    build_houzz_search_url,
    build_google_search_url,
    build_duckduckgo_search_url,
    build_contact_search_url,
)
from ai.deepseek_middleware import execute_llm_payload, DEEPSEEK_SCOUT_MODEL
from validation.gate_footprint import check_footprint
from validation.gate_smtp import check_smtp
from dedup.hash_dedup import compute_hash, check_duplicate


MAX_LEADS = 50


async def run_web_absent(
    query: str,
    user_tier: str = "free",
) -> List[Dict[str, Any]]:
    """Find businesses without their own website — only on aggregator sites."""

    leads = []

    # --------------------------------------------------------
    # PHASE 1: SCRAPLING — Fetch real data from aggregator sites
    # --------------------------------------------------------
    print(f"[WEB_ABSENT] Scrapling Phase: Fetching aggregator data for '{query}'")

    scraped_texts = []

    # Source 1: Yelp search
    yelp_url = build_yelp_search_url(query)
    yelp_result = await stealth_fetch(yelp_url)
    if yelp_result:
        text = extract_text_from_html(yelp_result["html"])
        links = extract_links_from_html(yelp_result["html"], base_url="https://www.yelp.com")
        if text:
            scraped_texts.append({"source": "Yelp", "content": text, "links": links})
            print(f"[WEB_ABSENT] Scraped Yelp: {len(text)} chars, {len(links)} links")
    else:
        print(f"[WEB_ABSENT] Yelp fetch failed — continuing with other sources")

    # Source 2: Houzz search
    houzz_url = build_houzz_search_url(query)
    houzz_result = await stealth_fetch(houzz_url)
    if houzz_result:
        text = extract_text_from_html(houzz_result["html"])
        links = extract_links_from_html(houzz_result["html"], base_url="https://www.houzz.com")
        if text:
            scraped_texts.append({"source": "Houzz", "content": text, "links": links})
            print(f"[WEB_ABSENT] Scraped Houzz: {len(text)} chars, {len(links)} links")
    else:
        print(f"[WEB_ABSENT] Houzz fetch failed")

    # Source 3: Google search for aggregator listings
    google_url = build_google_search_url(f"{query} site:yelp.com OR site:houzz.com OR site:etsy.com")
    google_result = await stealth_fetch(google_url)
    if google_result:
        text = extract_text_from_html(google_result["html"])
        if text:
            scraped_texts.append({"source": "Google Search (aggregator focus)", "content": text})
            print(f"[WEB_ABSENT] Scraped Google Search: {len(text)} chars")
    else:
        print(f"[WEB_ABSENT] Google Search fetch failed")

    # Source 4: DuckDuckGo search for aggregator listings
    ddg_url = build_duckduckgo_search_url(f"{query} yelp OR houzz OR etsy no website")
    ddg_result = await stealth_fetch(ddg_url)
    if ddg_result:
        text = extract_text_from_html(ddg_result["html"])
        if text:
            scraped_texts.append({"source": "DuckDuckGo", "content": text})
            print(f"[WEB_ABSENT] Scraped DuckDuckGo: {len(text)} chars")
    else:
        print(f"[WEB_ABSENT] DuckDuckGo fetch failed")

    if not scraped_texts:
        print(f"[WEB_ABSENT] All Scrapling sources failed — using DeepSeek knowledge fallback")

    # Combine scraped content
    combined_text = ""
    if scraped_texts:
        combined_text = "\n\n".join(
            f"--- SOURCE: {s['source']} ---\n{s['content']}"
            for s in scraped_texts
        )

    # Collect external links
    all_external_links = set()
    aggregator_domains = {"yelp.com", "houzz.com", "zillow.com", "etsy.com", "amazon.com",
                          "facebook.com", "google.com", "instagram.com"}
    for s in scraped_texts:
        for link in s.get("links", []):
            try:
                domain = urlparse(link).netloc.lower()
                if domain and not any(agg in domain for agg in aggregator_domains):
                    all_external_links.add(link)
            except:
                pass

    # --------------------------------------------------------
    # PHASE 2: DEEPSEEK — Structure the data
    # --------------------------------------------------------
    print(f"[WEB_ABSENT] DeepSeek Phase: Structuring data")

    if combined_text:
        structure_prompt = f"""
        You are a business data extractor. Below is REAL TEXT scraped from the internet
        about businesses listed on aggregator platforms related to: "{query}"

        Extract REAL businesses mentioned in this text.
        HARD RULES:
        - The business must NOT have its own standalone website
        - The business must exist ONLY on the aggregator platform

        SCRAPED CONTENT:
        {combined_text[:12000]}

        For each REAL business you find, provide:
        - company_name: The exact business name
        - aggregator_source: Which platform they are on (Yelp, Houzz, etc.)
        - aggregator_url: Direct URL to their profile (or "ABSENT")
        - phone: Phone number if mentioned (or "ABSENT")
        - address: Address if mentioned (or "ABSENT")
        - has_external_website: true or false (MUST be false to be included)

        Return a JSON object with a "businesses" array. Find up to {MAX_LEADS} businesses.
        If you cannot find data for a field, write "ABSENT".
        """
    else:
        structure_prompt = f"""
        You are a business data researcher. Find real businesses related to: "{query}"
        that exist ONLY on aggregator platforms (Yelp, Houzz, Etsy, etc.) and do NOT
        have their own website.

        Include as many REAL businesses as you can — aim for {MAX_LEADS}.

        For each business, provide:
        - company_name: The real business name
        - aggregator_source: Which platform they are on (Yelp, Houzz, etc.)
        - aggregator_url: Direct URL to their profile (or "ABSENT")
        - phone: Phone number if known (or "ABSENT")
        - address: Address if known (or "ABSENT")
        - has_external_website: false (these businesses don't have websites)

        Return a JSON object with a "businesses" array. Find up to {MAX_LEADS} businesses.
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
        print(f"[WEB_ABSENT] DeepSeek structuring error: {e}")
        businesses = []

    print(f"[WEB_ABSENT] DeepSeek extracted {len(businesses)} candidate businesses")

    # --------------------------------------------------------
    # PHASE 3: FILTER, VALIDATE & ENRICH
    # --------------------------------------------------------
    for biz in businesses[:MAX_LEADS]:
        company_name = biz.get("company_name", "ABSENT")
        aggregator_source = biz.get("aggregator_source", "ABSENT")
        aggregator_url = biz.get("aggregator_url", "ABSENT")
        has_external_website = biz.get("has_external_website", True)
        biz_phone = biz.get("phone", "ABSENT")
        biz_address = biz.get("address", "ABSENT")

        if company_name == "ABSENT" or not company_name:
            continue

        if has_external_website is True or has_external_website == "true":
            print(f"[WEB_ABSENT] {company_name} has external website — DROPPED")
            continue

        if aggregator_url != "ABSENT" and aggregator_url in all_external_links:
            print(f"[WEB_ABSENT] {company_name} aggregator URL links to external site — DROPPED")
            continue

        url_to_hash = aggregator_url if aggregator_url != "ABSENT" else company_name
        domain_hash = compute_hash(url_to_hash)

        is_dup, cached_data = await check_duplicate(domain_hash)
        if is_dup and cached_data:
            leads.append(cached_data)
            continue

        # Multi-source enrichment
        enrichment = await _enrich_aggregator_lead(
            company_name, aggregator_source, aggregator_url, user_tier
        )

        # Prefer enrichment phone, fall back to structure-phase phone
        final_phone = enrichment.get("phone", "ABSENT")
        if biz_phone != "ABSENT" and final_phone == "ABSENT":
            final_phone = biz_phone

        lead = {
            "domain_hash": domain_hash,
            "company_name": company_name,
            "website_url": aggregator_url,
            "dm_name": enrichment.get("dm_name", "ABSENT"),
            "dm_position": enrichment.get("dm_position", "ABSENT"),
            "verified_email": enrichment.get("verified_email", "ABSENT"),
            "is_catchall": False,
            "linkedin": "ABSENT",
            "instagram": "ABSENT",
            "phone": final_phone,
            "aggregator_source": aggregator_source,
            "aggregator_url": aggregator_url,
            "address": biz_address,
        }

        # Gate 2: Footprint (Starter+)
        if user_tier in ("starter", "growth", "pro"):
            footprint_ok = check_footprint(lead)
            if not footprint_ok:
                print(f"[WEB_ABSENT] Footprint failed for {company_name} — DROPPED")
                continue

        # Gate 3: SMTP (Pro only)
        if user_tier == "pro" and lead["verified_email"] != "ABSENT":
            smtp_ok, is_catchall = await check_smtp(lead["verified_email"])
            lead["is_catchall"] = is_catchall
            if not smtp_ok and not is_catchall:
                print(f"[WEB_ABSENT] SMTP failed for {lead['verified_email']} — DROPPED")
                continue

        leads.append(lead)

    return leads


async def _enrich_aggregator_lead(
    company_name: str,
    aggregator_source: str,
    aggregator_url: str,
    user_tier: str,
) -> Dict[str, str]:
    """
    Multi-source enrichment for aggregator-listed businesses:
    1. Try scraping the aggregator profile page
    2. If that fails → DuckDuckGo search for contact details
    3. DeepSeek extracts structured info from whichever source succeeds
    """
    scraped_text = ""
    source_description = ""

    # STRATEGY 1: Try scraping the aggregator profile page
    if aggregator_url and aggregator_url != "ABSENT":
        print(f"[WEB_ABSENT] Scrapling: Fetching aggregator profile {aggregator_url}")
        profile_result = await stealth_fetch(aggregator_url)
        if profile_result:
            scraped_text = extract_text_from_html(profile_result["html"], max_chars=8000)
            if scraped_text:
                print(f"[WEB_ABSENT] Scraped aggregator profile: {len(scraped_text)} chars")
                source_description = f"the {aggregator_source} profile of {company_name}"

    # STRATEGY 2: Web search for contact details
    if not scraped_text:
        print(f"[WEB_ABSENT] Profile scraping failed — searching for contact details of {company_name}")
        search_url = build_contact_search_url(company_name)
        search_result = await stealth_fetch(search_url)
        if search_result:
            scraped_text = extract_text_from_html(search_result["html"], max_chars=8000)
            if scraped_text:
                print(f"[WEB_ABSENT] Found contact info via web search: {len(scraped_text)} chars")
                source_description = f"web search results for '{company_name}' contact details"

    if scraped_text:
        prompt = f"""
        You are a business researcher. Below is REAL TEXT from {source_description}.

        This business does NOT have its own website. Extract contact details:
        - dm_name: Full name of the owner or manager
        - dm_position: Their job title
        - verified_email: Any email address listed
        - phone: Phone number listed

        IMPORTANT: Only extract information clearly present in the text.
        Do NOT guess or generate email addresses. If not found, write "ABSENT".

        SOURCE TEXT:
        {scraped_text[:8000]}

        Return a single JSON object.
        """
    else:
        prompt = f"""
        You are a business researcher. Find contact details for:
        "{company_name}" listed on {aggregator_source} at {aggregator_url}

        This business does NOT have its own website. Look for:
        - dm_name: Full name of the owner or manager
        - dm_position: Their job title
        - verified_email: Any email address (only if CERTAIN)
        - phone: Phone number (only if CERTAIN)

        CRITICAL: Only provide information you are CONFIDENT is accurate.
        Do NOT guess. It is better to return "ABSENT" than wrong information.

        Return a single JSON object.
        """

    try:
        response = await execute_llm_payload({
            "model": DEEPSEEK_SCOUT_MODEL,
            "messages": [
                {"role": "system", "content": "You are a precise data extractor. Only extract REAL, VERIFIED contact information. Never invent or guess data. Always respond with valid JSON. Use 'ABSENT' for missing data."},
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        })

        content = response.get("choices", [{}])[0].get("message", {}).get("content", "{}")
        return json.loads(content)

    except Exception as e:
        print(f"[WEB_ABSENT] Enrichment error for {company_name}: {e}")
        return {
            "dm_name": "ABSENT",
            "dm_position": "ABSENT",
            "verified_email": "ABSENT",
            "phone": "ABSENT",
        }
