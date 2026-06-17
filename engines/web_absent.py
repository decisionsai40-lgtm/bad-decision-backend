"""
BAD DECISION — Engine 3: Web-Absent Businesses
===============================================
This engine finds businesses that exist ONLY on aggregator sites
(Yelp, Houzz, Etsy) and do NOT have their own website.

PIPELINE:
  1. Fetch aggregator data (Serper.dev in Tier 3, Scrapling for now)
  2. DeepSeek structures the scraped text into clean lead objects
  3. HARD FILTER: Drop any business that HAS an external website link
  4. Validation gates run (Footprint pre-filter + SMTP + DeepSeek based on tier)
  5. Skip Gate 1 (DNS) — these businesses have no website to check

HARD RULE: If the profile has a link to an external http domain,
DROP the row. These businesses need a website built for them.
"""

import json
from urllib.parse import urlparse
from typing import List, Dict, Any, Callable, Optional

from scraping.stealth_fetcher import (
    stealth_fetch,
    extract_text_from_html,
    extract_links_from_html,
    build_yelp_search_url,
    build_houzz_search_url,
    build_google_search_url,
)
from ai.deepseek_middleware import execute_llm_payload, DEEPSEEK_SCOUT_MODEL
from validation.gate_footprint import check_footprint
from validation.gate_smtp import check_smtp
from validation.gate_deepseek import check_deepseek
from dedup.hash_dedup import compute_domain_hash
from config import LEAD_TARGET_FREE, LEAD_TARGET_PAID


async def run_web_absent(
    query: str,
    user_tier: str = "free",
    country: str = "",
    state_region: str = "",
    progress_callback: Optional[Callable] = None,
) -> List[Dict[str, Any]]:
    """Find businesses without their own website — only on aggregator sites."""
    leads = []
    lead_target = LEAD_TARGET_PAID if user_tier != "free" else LEAD_TARGET_FREE

    # --------------------------------------------------------
    # PHASE 1: Fetch real data from aggregator sites
    # --------------------------------------------------------
    if progress_callback:
        await progress_callback(15, "Searching Yelp, Houzz, and Etsy for businesses without websites...")

    print(f"[WEB_ABSENT] Fetching aggregator data for '{query}'")

    scraped_texts = []

    # Source 1: Yelp search
    yelp_url = build_yelp_search_url(query, state_region or country)
    yelp_result = await stealth_fetch(yelp_url, timeout=15)
    if yelp_result:
        text = extract_text_from_html(yelp_result["html"])
        links = extract_links_from_html(yelp_result["html"], base_url="https://www.yelp.com")
        if text:
            scraped_texts.append({"source": "Yelp", "content": text, "links": links})
            print(f"[WEB_ABSENT] Scraped Yelp: {len(text)} chars, {len(links)} links")

    # Source 2: Houzz search
    houzz_url = build_houzz_search_url(query)
    houzz_result = await stealth_fetch(houzz_url, timeout=15)
    if houzz_result:
        text = extract_text_from_html(houzz_result["html"])
        links = extract_links_from_html(houzz_result["html"], base_url="https://www.houzz.com")
        if text:
            scraped_texts.append({"source": "Houzz", "content": text, "links": links})
            print(f"[WEB_ABSENT] Scraped Houzz: {len(text)} chars, {len(links)} links")

    # Source 3: Google search for businesses on aggregator sites
    google_url = build_google_search_url(f"{query} site:yelp.com OR site:houzz.com OR site:etsy.com")
    google_result = await stealth_fetch(google_url, timeout=15)
    if google_result:
        text = extract_text_from_html(google_result["html"])
        if text:
            scraped_texts.append({"source": "Google Search (aggregator focus)", "content": text})
            print(f"[WEB_ABSENT] Scraped Google Search: {len(text)} chars")

    if not scraped_texts:
        print(f"[WEB_ABSENT] All sources failed — no data to process")
        return []

    combined_text = "\n\n".join(
        f"--- SOURCE: {s['source']} ---\n{s['content']}"
        for s in scraped_texts
    )

    # Collect all external links found in the scraped pages
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
    # PHASE 2: DeepSeek — Structure the scraped data
    # --------------------------------------------------------
    if progress_callback:
        await progress_callback(35, "AI is analyzing aggregator data and extracting business names...")

    print(f"[WEB_ABSENT] DeepSeek structuring phase")

    structure_prompt = f"""
    You are a business data extractor. Below is REAL TEXT scraped from the internet
    about businesses listed on aggregator platforms related to: "{query}"

    Your job is to extract REAL businesses mentioned in this text.
    Do NOT invent or hallucinate businesses that are not in the text.

    HARD RULES:
    - The business must NOT have its own standalone website
    - The business must exist ONLY on the aggregator platform
    - If a business has a link to an external website, EXCLUDE it

    SCRAPED CONTENT:
    {combined_text[:12000]}

    For each REAL business you find, provide:
    - company_name: The exact business name as mentioned
    - aggregator_source: Which platform they are on (Yelp, Houzz, etc.)
    - aggregator_url: Direct URL to their profile on the aggregator (or "ABSENT")
    - has_external_website: true or false (MUST be false to be included)

    Return a JSON object with a "businesses" array. Find up to {lead_target} businesses.
    If you cannot find data for a field, write "ABSENT".

    Example format:
    {{
        "businesses": [
            {{
                "company_name": "Sunset Bakery",
                "aggregator_source": "Yelp",
                "aggregator_url": "https://yelp.com/biz/sunset-bakery",
                "has_external_website": false
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
        print(f"[WEB_ABSENT] DeepSeek structuring error: {e}")

    print(f"[WEB_ABSENT] DeepSeek extracted {len(businesses)} candidate businesses")

    # --------------------------------------------------------
    # PHASE 3: FILTER, VALIDATE & ENRICH
    # --------------------------------------------------------
    if progress_callback:
        await progress_callback(50, f"Filtering and enriching {min(len(businesses), lead_target)} businesses...")

    for biz in businesses[:lead_target]:
        company_name = biz.get("company_name", "ABSENT")
        aggregator_source = biz.get("aggregator_source", "ABSENT")
        aggregator_url = biz.get("aggregator_url", "ABSENT")
        has_external_website = biz.get("has_external_website", True)

        if company_name == "ABSENT" or not company_name:
            continue

        # HARD FILTER: Must NOT have an external website
        if has_external_website is True or has_external_website == "true":
            print(f"[WEB_ABSENT] {company_name} has external website — DROPPED")
            continue

        if aggregator_url != "ABSENT" and aggregator_url in all_external_links:
            print(f"[WEB_ABSENT] {company_name} aggregator URL links to external site — DROPPED")
            continue

        domain_hash = compute_domain_hash(aggregator_url if aggregator_url != "ABSENT" else company_name)

        # NOTE: We SKIP Gate 1 (DNS) for web-absent businesses — no website to check.
        gates_passed = 0

        # Enrich with DeepSeek
        enrichment = await _enrich_aggregator_lead(
            company_name, aggregator_source, aggregator_url, user_tier
        )

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
            "facebook": "ABSENT",
            "phone": enrichment.get("phone", "ABSENT"),
            "aggregator_source": aggregator_source,
            "aggregator_url": aggregator_url,
            "validation_gates_passed": gates_passed,
        }

        # Pre-filter: Footprint check
        if not check_footprint(lead):
            print(f"[WEB_ABSENT] Footprint failed for {company_name} — DROPPED")
            continue

        # Gate 2: SMTP (Starter+)
        if user_tier in ("starter", "growth", "pro") and lead["verified_email"] != "ABSENT":
            smtp_ok, is_catchall = await check_smtp(lead["verified_email"])
            lead["is_catchall"] = is_catchall
            if not smtp_ok and not is_catchall:
                print(f"[WEB_ABSENT] SMTP failed for {lead['verified_email']} — DROPPED")
                continue
            gates_passed = 2
            lead["validation_gates_passed"] = gates_passed

        # Gate 3: DeepSeek AI (Pro only)
        if user_tier == "pro" and lead["verified_email"] != "ABSENT":
            deepseek_ok, is_role, reason = await check_deepseek(lead["verified_email"], company_name)
            if not deepseek_ok:
                print(f"[WEB_ABSENT] DeepSeek Gate 3 rejected {lead['verified_email']} — DROPPED")
                continue
            if is_role:
                lead["is_catchall"] = True
            gates_passed = 3
            lead["validation_gates_passed"] = gates_passed

        leads.append(lead)

    print(f"[WEB_ABSENT] Returning {len(leads)} verified leads")
    return leads


async def _enrich_aggregator_lead(
    company_name: str,
    aggregator_source: str,
    aggregator_url: str,
    user_tier: str,
) -> Dict[str, str]:
    """Enrich an aggregator-listed business with contact details."""
    scraped_profile_text = ""

    if aggregator_url and aggregator_url != "ABSENT":
        print(f"[WEB_ABSENT] Fetching aggregator profile {aggregator_url}")
        profile_result = await stealth_fetch(aggregator_url, timeout=15)
        if profile_result:
            scraped_profile_text = extract_text_from_html(profile_result["html"], max_chars=8000)

    if scraped_profile_text:
        prompt = f"""
        You are a business researcher. Below is REAL TEXT scraped from the {aggregator_source} profile page of:
        "{company_name}" at {aggregator_url}

        This business does NOT have its own website. Extract contact details from the scraped profile text:
        - dm_name: Full name of the owner or manager
        - dm_position: Their job title
        - verified_email: Any email address listed (from the aggregator profile)
        - phone: Phone number listed on the profile

        SCRAPED PROFILE CONTENT:
        {scraped_profile_text[:8000]}

        Only extract information that is clearly present in the scraped text.
        If you cannot find any piece of information, write "ABSENT".
        Return a single JSON object.
        """
    else:
        prompt = f"""
        You are a business researcher. Find contact details for this business:
        "{company_name}" listed on {aggregator_source} at {aggregator_url}

        This business does NOT have its own website. Look for:
        - dm_name: Full name of the owner or manager
        - dm_position: Their job title
        - verified_email: Any email address listed (from the aggregator profile)
        - phone: Phone number listed on the profile

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
        print(f"[WEB_ABSENT] Enrichment error for {company_name}: {e}")
        return {
            "dm_name": "ABSENT",
            "dm_position": "ABSENT",
            "verified_email": "ABSENT",
            "phone": "ABSENT",
        }
