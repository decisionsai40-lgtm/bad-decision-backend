"""
BAD DECISION — Engine 1: Ads Intelligence
==========================================
This engine finds businesses that are actively running ads
(Facebook, Google, TikTok).

PIPELINE:
  1. Fetch ad library data + search results (Serper.dev in Tier 3, Scrapling for now)
  2. DeepSeek structures the scraped text into clean lead objects
  3. DeepSeek enriches each lead with decision maker details
  4. Validation gates run based on tier:
       - Free: Gate 1 (DNS — domain exists + MX)
       - Starter/Growth: Gate 1 + Gate 2 (DNS + SMTP)
       - Pro: Gate 1 + Gate 2 + Gate 3 (DNS + SMTP + DeepSeek)

Why? If a business is spending money on ads, they have a marketing budget.
That makes them a hot lead for agencies and service providers.
"""

import json
from typing import List, Dict, Any, Callable, Optional

from scraping.stealth_fetcher import (
    stealth_fetch,
    extract_text_from_html,
    build_meta_ads_library_url,
    build_google_search_url,
)
from ai.deepseek_middleware import execute_llm_payload, DEEPSEEK_SCOUT_MODEL
from validation.gate_dns import check_dns
from validation.gate_footprint import check_footprint
from validation.gate_smtp import check_smtp
from validation.gate_deepseek import check_deepseek, is_role_address
from dedup.hash_dedup import compute_domain_hash
from config import LEAD_TARGET_FREE, LEAD_TARGET_PAID


async def run_ads_intent(
    query: str,
    user_tier: str = "free",
    country: str = "",
    state_region: str = "",
    progress_callback: Optional[Callable] = None,
) -> List[Dict[str, Any]]:
    """
    Search for companies running ads based on the user's query.

    Args:
        query: What the user typed (e.g., "roofers in Texas")
        user_tier: Their subscription level (free/starter/growth/pro)
        country: Country code (e.g., "US")
        state_region: State/region name
        progress_callback: Async callback(progress: int, step: str) for UI updates

    Returns:
        List of lead dictionaries with all extracted data
    """
    leads = []
    lead_target = LEAD_TARGET_PAID if user_tier != "free" else LEAD_TARGET_FREE

    # --------------------------------------------------------
    # PHASE 1: Fetch real data from the web
    # --------------------------------------------------------
    if progress_callback:
        await progress_callback(15, "Searching ad libraries and Google for businesses running ads...")

    print(f"[ADS_INTENT] Fetching ad data for '{query}'")

    scraped_texts = []

    # Source 1: Meta Ads Library (public, no login required)
    meta_url = build_meta_ads_library_url(query)
    meta_result = await stealth_fetch(meta_url, timeout=15)
    if meta_result:
        text = extract_text_from_html(meta_result["html"])
        if text:
            scraped_texts.append({"source": "Meta Ads Library", "content": text})
            print(f"[ADS_INTENT] Scraped Meta Ads Library: {len(text)} chars")

    # Source 2: Google search for businesses running ads
    google_url = build_google_search_url(f"{query} running ads advertising")
    google_result = await stealth_fetch(google_url, timeout=15)
    if google_result:
        text = extract_text_from_html(google_result["html"])
        if text:
            scraped_texts.append({"source": "Google Search", "content": text})
            print(f"[ADS_INTENT] Scraped Google Search: {len(text)} chars")

    if not scraped_texts:
        print(f"[ADS_INTENT] All sources failed — no data to process")
        return []

    combined_text = "\n\n".join(
        f"--- SOURCE: {s['source']} ---\n{s['content']}"
        for s in scraped_texts
    )

    # --------------------------------------------------------
    # PHASE 2: DeepSeek — Structure the scraped data
    # --------------------------------------------------------
    if progress_callback:
        await progress_callback(35, "AI is analyzing scraped data and extracting business names...")

    print(f"[ADS_INTENT] DeepSeek structuring phase")

    structure_prompt = f"""
    You are a business data extractor. Below is REAL TEXT scraped from the internet
    about businesses related to: "{query}"

    Your job is to extract REAL businesses mentioned in this text.
    Do NOT invent or hallucinate businesses that are not in the text.

    SCRAPED CONTENT:
    {combined_text[:12000]}

    For each REAL business you find, provide:
    - company_name: The exact business name as mentioned
    - website_url: Their website domain if mentioned (or "ABSENT")
    - ad_platform: Which platform they advertise on if mentioned (or "ABSENT")

    Return a JSON object with a "businesses" array. Find up to {lead_target} businesses.
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

    businesses = []
    try:
        response = await execute_llm_payload({
            "model": DEEPSEEK_SCOUT_MODEL,
            "messages": [
                {"role": "system", "content": "You are a precise data extractor. Only extract REAL businesses mentioned in the provided text. Never invent data. Always respond with valid JSON. Use 'ABSENT' for missing data."},
                {"role": "user", "content": structure_prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        })

        content = response.get("choices", [{}])[0].get("message", {}).get("content", "{}")
        parsed = json.loads(content)
        businesses = parsed.get("businesses", parsed.get("results", []))
        if isinstance(parsed, list):
            businesses = parsed

    except Exception as e:
        print(f"[ADS_INTENT] DeepSeek structuring error: {e}")

    print(f"[ADS_INTENT] DeepSeek extracted {len(businesses)} candidate businesses")

    # --------------------------------------------------------
    # PHASE 3: VALIDATE & ENRICH each business
    # --------------------------------------------------------
    if progress_callback:
        await progress_callback(50, f"Validating and enriching {min(len(businesses), lead_target)} businesses...")

    for biz in businesses[:lead_target]:
        company_name = biz.get("company_name", "ABSENT")
        website_url = biz.get("website_url", "ABSENT")
        ad_platform = biz.get("ad_platform", "ABSENT")

        if company_name == "ABSENT" or not company_name:
            continue

        domain_hash = compute_domain_hash(website_url if website_url != "ABSENT" else company_name)

        # Gate 1: DNS Check (ALL tiers)
        gates_passed = 0
        if website_url != "ABSENT":
            domain_ok, has_mx = await check_dns(website_url)
            if not domain_ok:
                print(f"[ADS_INTENT] DNS failed for {website_url} — DROPPED")
                continue
            gates_passed = 1

        # Enrich with DeepSeek
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
            "facebook": enrichment.get("facebook", "ABSENT"),
            "phone": enrichment.get("phone", "ABSENT"),
            "ad_platform": ad_platform,
            "validation_gates_passed": gates_passed,
        }

        # Pre-filter: Footprint check (all tiers — drops leads with zero contact methods)
        if not check_footprint(lead):
            print(f"[ADS_INTENT] Footprint failed for {company_name} — no contact method, DROPPED")
            continue

        # Gate 2: SMTP Check (Starter, Growth, Pro)
        if user_tier in ("starter", "growth", "pro") and lead["verified_email"] != "ABSENT":
            smtp_ok, is_catchall = await check_smtp(lead["verified_email"])
            lead["is_catchall"] = is_catchall
            if not smtp_ok and not is_catchall:
                print(f"[ADS_INTENT] SMTP failed for {lead['verified_email']} — DROPPED")
                continue
            gates_passed = 2
            lead["validation_gates_passed"] = gates_passed

        # Gate 3: DeepSeek AI Check (Pro only)
        if user_tier == "pro" and lead["verified_email"] != "ABSENT":
            deepseek_ok, is_role, reason = await check_deepseek(lead["verified_email"], company_name)
            if not deepseek_ok:
                print(f"[ADS_INTENT] DeepSeek Gate 3 rejected {lead['verified_email']}: {reason} — DROPPED")
                continue
            if is_role:
                lead["is_catchall"] = True  # Flag role addresses
            gates_passed = 3
            lead["validation_gates_passed"] = gates_passed

        leads.append(lead)

    print(f"[ADS_INTENT] Returning {len(leads)} verified leads")
    return leads


async def _enrich_lead(
    company_name: str,
    website_url: str,
    user_tier: str,
) -> Dict[str, str]:
    """Enrich a lead with decision maker details using DeepSeek + website scraping."""
    scraped_website_text = ""

    if website_url and website_url != "ABSENT":
        print(f"[ADS_INTENT] Fetching company website {website_url}")
        site_result = await stealth_fetch(website_url, timeout=15)
        if site_result:
            scraped_website_text = extract_text_from_html(site_result["html"], max_chars=8000)

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
        - facebook: The company's Facebook URL
        - phone: The company phone number

        SCRAPED WEBSITE CONTENT:
        {scraped_website_text[:8000]}

        Only extract information that is clearly present in the scraped text.
        If you cannot find any piece of information, you MUST write "ABSENT".
        Return a single JSON object.
        """
    else:
        prompt = f"""
        You are an expert business researcher. Find the key decision maker
        for this company: "{company_name}" ({website_url})

        Look for:
        - dm_name: Full name of the CEO, founder, or owner
        - dm_position: Their job title
        - verified_email: Their work email address
        - linkedin: Their LinkedIn profile URL
        - instagram: The company's Instagram URL
        - facebook: The company's Facebook URL
        - phone: The company phone number

        If you cannot find any piece of information, write "ABSENT".
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
            "facebook": "ABSENT",
            "phone": "ABSENT",
        }
