"""
BAD DECISION AI — Engine 1: Digital Ads Intelligence (Scrapling-First + DeepSeek Fallback)
=========================================================================================
This engine scrapes Meta Ads Library and Google search to find
businesses that are actively running ads.

PIPELINE (as specified in TRD Section 4):
1. Scrapling fetches REAL data from ad libraries and search engines
2. DeepSeek structures the scraped HTML/text into clean lead objects
3. Validation gates run (DNS → Footprint → SMTP based on tier)
4. Dedup & cache

FALLBACK: If all Scrapling sources fail (JS-heavy pages), we use
DeepSeek's knowledge to find businesses matching the query. This is
less reliable than scraping but ensures the user always gets results.

Why? If a business is spending money on ads, they have a marketing budget.
That makes them a hot lead for agencies and service providers.
"""

import json
from typing import List, Dict, Any

from scraping.stealth_fetcher import (
    stealth_fetch,
    extract_text_from_html,
    build_meta_ads_library_url,
    build_google_search_url,
    build_bing_search_url,
    build_duckduckgo_search_url,
)
from ai.deepseek_middleware import execute_llm_payload, DEEPSEEK_SCOUT_MODEL
from validation.gate_dns import check_dns
from validation.gate_footprint import check_footprint
from validation.gate_smtp import check_smtp
from dedup.hash_dedup import compute_hash, check_duplicate


async def run_ads_intent(
    query: str,
    user_tier: str = "free",
) -> List[Dict[str, Any]]:
    """
    Search for companies running ads based on the user's query.

    PIPELINE:
    1. Scrapling fetches pages from Meta Ads Library + search engines
    2. DeepSeek structures the raw scraped text into lead objects
    3. DeepSeek enriches each lead with DM contact details
    4. Validation gates run (DNS → Footprint → SMTP)

    Args:
        query: What the user typed (e.g., "roofers in Texas")
        user_tier: Their subscription level (free/starter/growth/pro)

    Returns:
        List of lead dictionaries with all extracted data
    """

    leads = []

    # --------------------------------------------------------
    # PHASE 1: SCRAPLING — Fetch real data from the web
    # --------------------------------------------------------
    print(f"[ADS_INTENT] Scrapling Phase: Fetching ad data for '{query}'")

    scraped_texts = []

    # Source 1: Meta Ads Library (public, no login required)
    meta_url = build_meta_ads_library_url(query)
    meta_result = await stealth_fetch(meta_url)
    if meta_result:
        text = extract_text_from_html(meta_result["html"])
        if text:
            scraped_texts.append({
                "source": "Meta Ads Library",
                "content": text,
            })
            print(f"[ADS_INTENT] Scraped Meta Ads Library: {len(text)} chars")
    else:
        print(f"[ADS_INTENT] Meta Ads Library fetch failed — continuing with other sources")

    # Source 2: Google search for businesses running ads
    google_url = build_google_search_url(f"{query} running ads advertising")
    google_result = await stealth_fetch(google_url)
    if google_result:
        text = extract_text_from_html(google_result["html"])
        if text:
            scraped_texts.append({
                "source": "Google Search",
                "content": text,
            })
            print(f"[ADS_INTENT] Scraped Google Search: {len(text)} chars")
    else:
        print(f"[ADS_INTENT] Google Search fetch failed")

    # Source 3: Bing search (often returns more text content)
    bing_url = build_bing_search_url(f"{query} running ads advertising")
    bing_result = await stealth_fetch(bing_url)
    if bing_result:
        text = extract_text_from_html(bing_result["html"])
        if text:
            scraped_texts.append({
                "source": "Bing Search",
                "content": text,
            })
            print(f"[ADS_INTENT] Scraped Bing Search: {len(text)} chars")
    else:
        print(f"[ADS_INTENT] Bing Search fetch failed")

    # Source 4: DuckDuckGo HTML (lightweight, less JS-heavy)
    ddg_url = build_duckduckgo_search_url(f"{query} running ads advertising business")
    ddg_result = await stealth_fetch(ddg_url)
    if ddg_result:
        text = extract_text_from_html(ddg_result["html"])
        if text:
            scraped_texts.append({
                "source": "DuckDuckGo",
                "content": text,
            })
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
        # We have scraped text — ask DeepSeek to extract from it
        structure_prompt = f"""
        You are a business data extractor. Below is REAL TEXT scraped from the internet
        about businesses related to: "{query}"

        Your job is to extract REAL businesses mentioned in this text.
        Do NOT invent or hallucinate businesses that are not in the text.
        Only extract companies that are clearly named in the scraped content.

        SCRAPED CONTENT:
        {combined_text[:12000]}

        For each REAL business you find, provide:
        - company_name: The exact business name as mentioned
        - website_url: Their website domain if mentioned (or "ABSENT" if not found)
        - ad_platform: Which platform they advertise on if mentioned (or "ABSENT")

        Return a JSON object with a "businesses" array. Find up to 25 businesses.
        If you cannot find data for a field, write "ABSENT".

        Example format:
        {{
            "businesses": [
                {{
                    "company_name": "ABC Roofing",
                    "website_url": "https://abcroofing.com",
                    "ad_platform": "Meta Ads"
                }}
            ]
        }}
        """
    else:
        # Fallback: No scraped text — use DeepSeek's knowledge
        structure_prompt = f"""
        You are a business data researcher. Find real businesses related to: "{query}"
        that are likely running digital ads (Google Ads, Meta Ads, etc.).

        Use your knowledge to identify businesses that would match this search.
        Focus on businesses that are actively advertising or have a marketing budget.

        For each business you find, provide:
        - company_name: The real business name
        - website_url: Their website URL (or "ABSENT" if unknown)
        - ad_platform: Which platform they likely advertise on (or "ABSENT")

        Return a JSON object with a "businesses" array. Find up to 15 businesses.
        If you cannot find data for a field, write "ABSENT".

        Example format:
        {{
            "businesses": [
                {{
                    "company_name": "ABC Roofing",
                    "website_url": "https://abcroofing.com",
                    "ad_platform": "Google Ads"
                }}
            ]
        }}
        """

    try:
        response = await execute_llm_payload({
            "model": DEEPSEEK_SCOUT_MODEL,
            "messages": [
                {"role": "system", "content": "You are a precise data extractor. Only extract REAL businesses. Never invent data. Always respond with valid JSON. Use 'ABSENT' for missing data."},
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
    for biz in businesses[:25]:
        company_name = biz.get("company_name", "ABSENT")
        website_url = biz.get("website_url", "ABSENT")
        ad_platform = biz.get("ad_platform", "ABSENT")

        # Skip if no company name
        if company_name == "ABSENT" or not company_name:
            continue

        # Compute hash for dedup check
        url_to_hash = website_url if website_url != "ABSENT" else company_name
        domain_hash = compute_hash(url_to_hash)

        # Check if we already have this lead cached
        is_dup, cached_data = await check_duplicate(domain_hash)
        if is_dup and cached_data:
            leads.append(cached_data)
            continue

        # --------------------------------------------------------
        # Gate 1: DNS Check (ALL tiers get this)
        # --------------------------------------------------------
        if website_url != "ABSENT":
            dns_ok = await check_dns(website_url)
            if not dns_ok:
                print(f"[ADS_INTENT] DNS failed for {website_url} — DROPPED")
                continue

        # --------------------------------------------------------
        # DeepSeek Enrichment — find decision maker details
        # --------------------------------------------------------
        enrichment = await _enrich_lead(company_name, website_url, user_tier)

        # Build the lead object
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

        # --------------------------------------------------------
        # Gate 2: Footprint Check (Starter tier and above)
        # --------------------------------------------------------
        if user_tier in ("starter", "growth", "pro"):
            footprint_ok = check_footprint(lead)
            if not footprint_ok:
                print(f"[ADS_INTENT] Footprint failed for {company_name} — DROPPED")
                continue

        # --------------------------------------------------------
        # Gate 3: SMTP Check (Pro tier only)
        # --------------------------------------------------------
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
    Enrich a lead with decision maker details.
    Uses Scrapling to visit the company website first (if available),
    then passes the scraped content to DeepSeek to extract contact info.

    PIPELINE:
    1. Scrapling fetches the company's website (if they have one)
    2. DeepSeek structures the website content into DM contact details
    3. Falls back to DeepSeek knowledge only if website fetch fails
    """
    scraped_website_text = ""

    # Try to scrape the company website for contact info
    if website_url and website_url != "ABSENT":
        print(f"[ADS_INTENT] Scrapling: Fetching company website {website_url}")
        site_result = await stealth_fetch(website_url)
        if site_result:
            scraped_website_text = extract_text_from_html(site_result["html"], max_chars=8000)
            print(f"[ADS_INTENT] Scraped company website: {len(scraped_website_text)} chars")

    # Build the enrichment prompt — use scraped content if available
    if scraped_website_text:
        prompt = f"""
        You are an expert business researcher. Below is REAL TEXT scraped from the website of:
        "{company_name}" ({website_url})

        Extract the following information from this scraped content:
        - dm_name: Full name of the CEO, founder, or owner
        - dm_position: Their job title (CEO, Founder, Owner, etc.)
        - verified_email: Their work email address
        - linkedin: Their LinkedIn profile URL
        - instagram: The company's Instagram URL
        - phone: The company phone number

        SCRAPED WEBSITE CONTENT:
        {scraped_website_text[:8000]}

        Only extract information that is clearly present in the scraped text.
        If you cannot find any piece of information, you MUST write "ABSENT" (not null, not empty).
        Return a single JSON object.
        """
    else:
        # Fallback: Ask DeepSeek to use its knowledge (less reliable but better than nothing)
        prompt = f"""
        You are an expert business researcher. Find the key decision maker
        for this company: "{company_name}" ({website_url})

        Look for:
        - dm_name: Full name of the CEO, founder, or owner
        - dm_position: Their job title (CEO, Founder, Owner, etc.)
        - verified_email: Their work email address
        - linkedin: Their LinkedIn profile URL
        - instagram: The company's Instagram URL
        - phone: The company phone number

        If you cannot find any piece of information, you MUST write "ABSENT" (not null, not empty).
        Return a single JSON object.
        """

    try:
        response = await execute_llm_payload({
            "model": DEEPSEEK_SCOUT_MODEL,
            "messages": [
                {"role": "system", "content": "You are a precise data extractor. Only extract information from the provided text when available. Never invent data. Always respond with valid JSON. Use 'ABSENT' for missing data."},
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
