"""
BAD DECISION AI — Engine 2: Local Businesses — Brick & Mortar (Scrapling-First)
================================================================================
This engine finds local businesses by scraping Google Maps search
results and Google search, then cross-referencing with OpenCorporates.

PIPELINE (as specified in TRD Section 4):
1. Scrapling fetches REAL data from Google Maps + Google search + OpenCorporates
2. DeepSeek structures the scraped HTML/text into clean lead objects
3. HARD FILTER: Drop any entity with > 50 employees or no physical address
4. Validation gates run (DNS → Footprint → SMTP based on tier)
5. Dedup & cache

HARD RULE: Drop any entity with > 50 employees or lacking
local physical coordinates. We only want small businesses.
"""

import json
from typing import List, Dict, Any

from scraping.stealth_fetcher import (
    stealth_fetch,
    extract_text_from_html,
    build_google_maps_url,
    build_google_search_url,
    build_opencorporates_url,
)
from ai.deepseek_middleware import execute_llm_payload, DEEPSEEK_SCOUT_MODEL
from validation.gate_dns import check_dns
from validation.gate_footprint import check_footprint
from validation.gate_smtp import check_smtp
from dedup.hash_dedup import compute_hash, check_duplicate


async def run_smb_maps(
    query: str,
    user_tier: str = "free",
) -> List[Dict[str, Any]]:
    """
    Find local brick-and-mortar businesses matching the user's query.

    PIPELINE:
    1. Scrapling fetches Google Maps + Google search + OpenCorporates
    2. DeepSeek structures scraped text into lead objects
    3. Hard filters: <50 employees, must have physical address
    4. DeepSeek enriches each lead with DM contact details
    5. Validation gates run based on tier
    """

    leads = []

    # --------------------------------------------------------
    # PHASE 1: SCRAPLING — Fetch real data from the web
    # --------------------------------------------------------
    print(f"[SMB_MAPS] Scrapling Phase: Fetching local business data for '{query}'")

    scraped_texts = []

    # Source 1: Google Maps search (public, no API key needed for basic search)
    maps_url = build_google_maps_url(query)
    maps_result = await stealth_fetch(maps_url)
    if maps_result:
        text = extract_text_from_html(maps_result["html"])
        if text:
            scraped_texts.append({
                "source": "Google Maps",
                "content": text,
            })
            print(f"[SMB_MAPS] Scraped Google Maps: {len(text)} chars")
    else:
        print(f"[SMB_MAPS] Google Maps fetch failed — continuing with other sources")

    # Source 2: Google search for local businesses
    google_url = build_google_search_url(f"{query} local small business near me")
    google_result = await stealth_fetch(google_url)
    if google_result:
        text = extract_text_from_html(google_result["html"])
        if text:
            scraped_texts.append({
                "source": "Google Search",
                "content": text,
            })
            print(f"[SMB_MAPS] Scraped Google Search: {len(text)} chars")
    else:
        print(f"[SMB_MAPS] Google Search fetch failed")

    # Source 3: OpenCorporates (public corporate registry)
    oc_url = build_opencorporates_url(query)
    oc_result = await stealth_fetch(oc_url)
    if oc_result:
        text = extract_text_from_html(oc_result["html"])
        if text:
            scraped_texts.append({
                "source": "OpenCorporates",
                "content": text,
            })
            print(f"[SMB_MAPS] Scraped OpenCorporates: {len(text)} chars")
    else:
        print(f"[SMB_MAPS] OpenCorporates fetch failed")

    if not scraped_texts:
        print(f"[SMB_MAPS] All Scrapling sources failed — no data to process")
        return []

    # Combine all scraped content for DeepSeek
    combined_text = "\n\n".join(
        f"--- SOURCE: {s['source']} ---\n{s['content']}"
        for s in scraped_texts
    )

    # --------------------------------------------------------
    # PHASE 2: DEEPSEEK — Structure the scraped data
    # --------------------------------------------------------
    print(f"[SMB_MAPS] DeepSeek Phase: Structuring scraped data")

    structure_prompt = f"""
    You are a local business data extractor. Below is REAL TEXT scraped from the internet
    about local businesses related to: "{query}"

    Your job is to extract REAL businesses mentioned in this text.
    Do NOT invent or hallucinate businesses that are not in the text.
    Only extract businesses that are clearly named in the scraped content.

    HARD RULES:
    - Each business MUST have fewer than 50 employees (if mentioned)
    - Each business MUST have a physical street address (no online-only businesses)
    - NO chains or large corporations

    SCRAPED CONTENT:
    {combined_text[:12000]}

    For each REAL business you find, provide:
    - company_name: The exact business name as mentioned
    - website_url: Their website (or "ABSENT" if not found)
    - address: Their physical street address if mentioned (or "ABSENT")
    - employee_count: Approximate number of employees if mentioned (or "ABSENT")

    Return a JSON object with a "businesses" array. Find up to 25 businesses.
    If you cannot find data for a field, write "ABSENT".

    Example format:
    {{
        "businesses": [
            {{
                "company_name": "Mike's Roofing LLC",
                "website_url": "https://mikesroofing.com",
                "address": "123 Main St, Dallas, TX",
                "employee_count": "8"
            }}
        ]
    }}
    """

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

        try:
            parsed = json.loads(content)
            businesses = parsed.get("businesses", parsed.get("results", []))
            if isinstance(parsed, list):
                businesses = parsed
        except json.JSONDecodeError:
            businesses = []

    except Exception as e:
        print(f"[SMB_MAPS] DeepSeek structuring error: {e}")
        businesses = []

    print(f"[SMB_MAPS] DeepSeek extracted {len(businesses)} candidate businesses from scraped data")

    # --------------------------------------------------------
    # PHASE 3: FILTER, VALIDATE & ENRICH each business
    # --------------------------------------------------------
    for biz in businesses[:25]:
        company_name = biz.get("company_name", "ABSENT")
        website_url = biz.get("website_url", "ABSENT")
        address = biz.get("address", "ABSENT")
        employee_count = biz.get("employee_count", "ABSENT")

        # HARD FILTER: Must have a company name
        if company_name == "ABSENT" or not company_name:
            continue

        # HARD FILTER: Must be < 50 employees
        try:
            if employee_count != "ABSENT" and int(str(employee_count).strip()) >= 50:
                print(f"[SMB_MAPS] {company_name} has {employee_count} employees — DROPPED (>50)")
                continue
        except (ValueError, TypeError):
            pass  # If we can't parse employee count, let it through

        # HARD FILTER: Must have physical address (coordinates)
        if address == "ABSENT" or not address:
            print(f"[SMB_MAPS] {company_name} has no physical address — DROPPED")
            continue

        # Dedup check
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
                print(f"[SMB_MAPS] DNS failed for {website_url} — DROPPED")
                continue

        # Enrich with DeepSeek (Scrapling visits website first)
        enrichment = await _enrich_local_lead(company_name, website_url, address, user_tier)

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
            "address": address,
        }

        # Gate 2: Footprint (Starter+)
        if user_tier in ("starter", "growth", "pro"):
            footprint_ok = check_footprint(lead)
            if not footprint_ok:
                print(f"[SMB_MAPS] Footprint failed for {company_name} — DROPPED")
                continue

        # Gate 3: SMTP (Pro only)
        if user_tier == "pro" and lead["verified_email"] != "ABSENT":
            smtp_ok, is_catchall = await check_smtp(lead["verified_email"])
            lead["is_catchall"] = is_catchall
            if not smtp_ok and not is_catchall:
                print(f"[SMB_MAPS] SMTP failed for {lead['verified_email']} — DROPPED")
                continue

        leads.append(lead)

    return leads


async def _enrich_local_lead(
    company_name: str,
    website_url: str,
    address: str,
    user_tier: str,
) -> Dict[str, str]:
    """
    Enrich a local business lead with DM details.
    Scrapling fetches the company website first, then DeepSeek extracts contact info.
    """
    scraped_website_text = ""

    # Try to scrape the company website for contact info
    if website_url and website_url != "ABSENT":
        print(f"[SMB_MAPS] Scrapling: Fetching company website {website_url}")
        site_result = await stealth_fetch(website_url)
        if site_result:
            scraped_website_text = extract_text_from_html(site_result["html"], max_chars=8000)
            print(f"[SMB_MAPS] Scraped company website: {len(scraped_website_text)} chars")

    if scraped_website_text:
        prompt = f"""
        You are a local business researcher. Below is REAL TEXT scraped from the website of:
        "{company_name}" located at "{address}" ({website_url})

        Extract the following information from this scraped content:
        - dm_name: Full name of the owner, CEO, or founder
        - dm_position: Their job title
        - verified_email: Their work or business email
        - linkedin: Their LinkedIn profile URL
        - instagram: The business Instagram URL
        - phone: The business phone number

        SCRAPED WEBSITE CONTENT:
        {scraped_website_text[:8000]}

        Only extract information that is clearly present in the scraped text.
        If you cannot find any piece of information, write "ABSENT".
        Return a single JSON object.
        """
    else:
        prompt = f"""
        You are a local business researcher. Find the owner or key decision maker for:
        "{company_name}" located at "{address}" ({website_url})

        Look for:
        - dm_name: Full name of the owner, CEO, or founder
        - dm_position: Their job title
        - verified_email: Their work or business email
        - linkedin: Their LinkedIn profile URL
        - instagram: The business Instagram URL
        - phone: The business phone number

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
        print(f"[SMB_MAPS] Enrichment error for {company_name}: {e}")
        return {
            "dm_name": "ABSENT",
            "dm_position": "ABSENT",
            "verified_email": "ABSENT",
            "linkedin": "ABSENT",
            "instagram": "ABSENT",
            "phone": "ABSENT",
        }
