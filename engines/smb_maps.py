"""
BAD DECISION — Engine 2: Local SMB Maps
========================================
This engine finds local brick-and-mortar businesses.

PIPELINE:
  1. Fetch local business data from OSM (Overpass + Nominatim) + Serper.dev CONCURRENTLY
     (OSM replaces Google Maps scraping — Google Maps requires JS and fails on Render free tier)
  2. DeepSeek structures the data into clean lead objects
  3. HARD FILTER: Drop any entity with > 50 employees or no physical address
  4. Validation gates run based on tier:
       - Free: Gate 1 (DNS)
       - Starter/Growth: Gate 1 + Gate 2 (DNS + SMTP)
       - Pro: Gate 1 + Gate 2 + Gate 3 (DNS + SMTP + DeepSeek)

HARD RULE: Drop any entity with > 50 employees or lacking
a physical address. We only want small businesses.
"""

import json
import asyncio
from typing import List, Dict, Any, Callable, Optional

from scraping.stealth_fetcher import (
    stealth_fetch,
    extract_text_from_html,
    build_opencorporates_url,
)
from scraping.osm_search import search_local_businesses
from scraping.serper_search import serper_search
from ai.deepseek_middleware import execute_llm_payload, DEEPSEEK_SCOUT_MODEL
from validation.gate_dns import check_dns
from validation.gate_footprint import check_footprint
from validation.gate_smtp import check_smtp
from validation.gate_deepseek import check_deepseek
from dedup.hash_dedup import compute_domain_hash
from config import LEAD_TARGET_FREE, LEAD_TARGET_PAID, SOURCE_TIMEOUT


async def run_smb_maps(
    query: str,
    user_tier: str = "free",
    country: str = "",
    state_region: str = "",
    progress_callback: Optional[Callable] = None,
) -> List[Dict[str, Any]]:
    """Find local brick-and-mortar businesses matching the user's query."""
    leads = []
    lead_target = LEAD_TARGET_PAID if user_tier != "free" else LEAD_TARGET_FREE

    # Build location string for search
    location_parts = [p for p in [state_region, country] if p]
    location = ", ".join(location_parts) if location_parts else ""
    search_query = f"{query} {location}".strip() if location else query

    # --------------------------------------------------------
    # PHASE 1: Fetch real data (CONCURRENTLY)
    # --------------------------------------------------------
    # PRIMARY: OpenStreetMap (Overpass + Nominatim) — replaces Google Maps
    # SUPPLEMENTARY: Serper.dev for Google search results
    # SUPPLEMENTARY: OpenCorporates for company registry data
    # All 3 sources fetched concurrently with asyncio.gather
    if progress_callback:
        await progress_callback(15, f"Searching OpenStreetMap for local businesses in {location or 'your area'}...")

    print(f"[SMB_MAPS] Fetching local business data for '{search_query}' (concurrent sources)")

    location_string = ", ".join([p for p in [state_region, country] if p]) if (state_region or country) else query

    osm_task = search_local_businesses(query, location=location_string, limit=lead_target)
    serper_task = serper_search(f"{search_query} local small business", num_results=20)
    oc_task = stealth_fetch(build_opencorporates_url(query), timeout=SOURCE_TIMEOUT)

    osm_businesses, serper_results, oc_result = await asyncio.gather(
        osm_task,
        serper_task,
        oc_task,
        return_exceptions=True,
    )

    scraped_texts = []
    osm_leads_direct = []  # OSM returns structured businesses we can use directly

    # Process OSM results (structured business data — can be used directly without DeepSeek)
    if isinstance(osm_businesses, list) and osm_businesses:
        print(f"[SMB_MAPS] OSM returned {len(osm_businesses)} businesses")
        # Convert OSM businesses to lead format directly (skip DeepSeek structuring for these)
        for biz in osm_businesses[:lead_target]:
            if biz.get("name"):
                osm_leads_direct.append({
                    "company_name": biz.get("name", "ABSENT"),
                    "website_url": biz.get("website", "ABSENT") or "ABSENT",
                    "address": biz.get("address", "ABSENT") or "ABSENT",
                    "phone": biz.get("phone", "ABSENT") or "ABSENT",
                    "email": biz.get("email", "ABSENT") or "ABSENT",
                    "source": "openstreetmap",
                })
    elif isinstance(osm_businesses, Exception):
        print(f"[SMB_MAPS] OSM error: {osm_businesses}")

    # Process Serper.dev results
    if isinstance(serper_results, list) and serper_results:
        serper_text = "\n".join(
            f"Title: {r.get('title', '')}\nURL: {r.get('link', '')}\nSnippet: {r.get('snippet', '')}"
            for r in serper_results
        )
        scraped_texts.append({"source": "Google Search (Serper.dev)", "content": serper_text})
        print(f"[SMB_MAPS] Serper.dev returned {len(serper_results)} results")
    elif isinstance(serper_results, Exception):
        print(f"[SMB_MAPS] Serper.dev error: {serper_results}")

    # Process OpenCorporates result
    if isinstance(oc_result, dict) and oc_result:
        text = extract_text_from_html(oc_result["html"])
        if text:
            scraped_texts.append({"source": "OpenCorporates", "content": text})
            print(f"[SMB_MAPS] Scraped OpenCorporates: {len(text)} chars")
    elif isinstance(oc_result, Exception):
        print(f"[SMB_MAPS] OpenCorporates error: {oc_result}")

    # If OSM gave us direct leads, process them through validation gates (skip DeepSeek structuring)
    if osm_leads_direct:
        if progress_callback:
            await progress_callback(40, f"Validating {len(osm_leads_direct)} businesses from OpenStreetMap...")

        for biz in osm_leads_direct:
            company_name = biz["company_name"]
            website_url = biz["website_url"]
            address = biz["address"]

            # HARD FILTER: Must have physical address
            if address == "ABSENT" or not address:
                continue

            domain_hash = compute_domain_hash(website_url if website_url != "ABSENT" else company_name)

            # Gate 1: DNS Check
            gates_passed = 0
            if website_url != "ABSENT":
                domain_ok, has_mx = await check_dns(website_url)
                if not domain_ok:
                    continue
                gates_passed = 1

            # Use OSM-provided contact data directly
            lead = {
                "domain_hash": domain_hash,
                "company_name": company_name,
                "website_url": website_url,
                "dm_name": "ABSENT",
                "dm_position": "ABSENT",
                "verified_email": biz.get("email", "ABSENT"),
                "is_catchall": False,
                "linkedin": "ABSENT",
                "instagram": "ABSENT",
                "facebook": "ABSENT",
                "phone": biz.get("phone", "ABSENT"),
                "address": address,
                "validation_gates_passed": gates_passed,
            }

            # Pre-filter: Footprint check
            if not check_footprint(lead):
                continue

            # Gate 2: SMTP (Starter+)
            if user_tier in ("starter", "growth", "pro") and lead["verified_email"] != "ABSENT":
                smtp_ok, is_catchall = await check_smtp(lead["verified_email"])
                lead["is_catchall"] = is_catchall
                if not smtp_ok and not is_catchall:
                    continue
                gates_passed = 2
                lead["validation_gates_passed"] = gates_passed

            # Gate 3: DeepSeek AI (Pro only)
            if user_tier == "pro" and lead["verified_email"] != "ABSENT":
                deepseek_ok, is_role, _ = await check_deepseek(lead["verified_email"], company_name)
                if not deepseek_ok:
                    continue
                if is_role:
                    lead["is_catchall"] = True
                gates_passed = 3
                lead["validation_gates_passed"] = gates_passed

            leads.append(lead)

        print(f"[SMB_MAPS] OSM direct leads after validation: {len(leads)}")

    # If we still need more leads, run DeepSeek structuring on the scraped text
    if len(leads) < lead_target and scraped_texts:
        combined_text = "\n\n".join(
            f"--- SOURCE: {s['source']} ---\n{s['content']}"
            for s in scraped_texts
        )
        # Fall through to DeepSeek structuring phase below
    elif not scraped_texts and not osm_leads_direct:
        print(f"[SMB_MAPS] All sources failed — no data to process")
        return leads
    else:
        # We have enough OSM leads and no scraped text to process
        print(f"[SMB_MAPS] Returning {len(leads)} leads from OSM")
        return leads

    # --------------------------------------------------------
    # PHASE 2: DeepSeek — Structure the scraped data
    # --------------------------------------------------------
    if progress_callback:
        await progress_callback(35, "AI is analyzing data and extracting local business names...")

    print(f"[SMB_MAPS] DeepSeek structuring phase")

    structure_prompt = f"""
    You are a local business data extractor. Below is REAL TEXT scraped from the internet
    about local businesses related to: "{search_query}"

    Your job is to extract REAL businesses mentioned in this text.
    Do NOT invent or hallucinate businesses that are not in the text.

    HARD RULES:
    - Each business MUST have fewer than 50 employees (if mentioned)
    - Each business MUST have a physical street address (no online-only businesses)
    - NO chains or large corporations

    SCRAPED CONTENT:
    {combined_text[:12000]}

    For each REAL business you find, provide:
    - company_name: The exact business name as mentioned
    - website_url: Their website (or "ABSENT")
    - address: Their physical street address if mentioned (or "ABSENT")
    - employee_count: Approximate number of employees if mentioned (or "ABSENT")

    Return a JSON object with a "businesses" array. Find up to {lead_target} businesses.
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
        print(f"[SMB_MAPS] DeepSeek structuring error: {e}")

    print(f"[SMB_MAPS] DeepSeek extracted {len(businesses)} candidate businesses")

    # --------------------------------------------------------
    # PHASE 3: FILTER, VALIDATE & ENRICH
    # --------------------------------------------------------
    if progress_callback:
        await progress_callback(50, f"Filtering and enriching {min(len(businesses), lead_target)} local businesses...")

    for biz in businesses[:lead_target]:
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
            pass

        # HARD FILTER: Must have physical address
        if address == "ABSENT" or not address:
            print(f"[SMB_MAPS] {company_name} has no physical address — DROPPED")
            continue

        domain_hash = compute_domain_hash(website_url if website_url != "ABSENT" else company_name)

        # Gate 1: DNS Check
        gates_passed = 0
        if website_url != "ABSENT":
            domain_ok, has_mx = await check_dns(website_url)
            if not domain_ok:
                print(f"[SMB_MAPS] DNS failed for {website_url} — DROPPED")
                continue
            gates_passed = 1

        # Enrich with DeepSeek
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
            "facebook": enrichment.get("facebook", "ABSENT"),
            "phone": enrichment.get("phone", "ABSENT"),
            "address": address,
            "validation_gates_passed": gates_passed,
        }

        # Pre-filter: Footprint check
        if not check_footprint(lead):
            print(f"[SMB_MAPS] Footprint failed for {company_name} — DROPPED")
            continue

        # Gate 2: SMTP (Starter+)
        if user_tier in ("starter", "growth", "pro") and lead["verified_email"] != "ABSENT":
            smtp_ok, is_catchall = await check_smtp(lead["verified_email"])
            lead["is_catchall"] = is_catchall
            if not smtp_ok and not is_catchall:
                print(f"[SMB_MAPS] SMTP failed for {lead['verified_email']} — DROPPED")
                continue
            gates_passed = 2
            lead["validation_gates_passed"] = gates_passed

        # Gate 3: DeepSeek AI (Pro only)
        if user_tier == "pro" and lead["verified_email"] != "ABSENT":
            deepseek_ok, is_role, reason = await check_deepseek(lead["verified_email"], company_name)
            if not deepseek_ok:
                print(f"[SMB_MAPS] DeepSeek Gate 3 rejected {lead['verified_email']} — DROPPED")
                continue
            if is_role:
                lead["is_catchall"] = True
            gates_passed = 3
            lead["validation_gates_passed"] = gates_passed

        leads.append(lead)

    print(f"[SMB_MAPS] Returning {len(leads)} verified leads")
    return leads


async def _enrich_local_lead(
    company_name: str,
    website_url: str,
    address: str,
    user_tier: str,
) -> Dict[str, str]:
    """Enrich a local business lead with DM details."""
    scraped_website_text = ""

    if website_url and website_url != "ABSENT":
        print(f"[SMB_MAPS] Fetching company website {website_url}")
        site_result = await stealth_fetch(website_url, timeout=15)
        if site_result:
            scraped_website_text = extract_text_from_html(site_result["html"], max_chars=8000)

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
        - facebook: The business Facebook URL
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
        - facebook: The business Facebook URL
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
            "facebook": "ABSENT",
            "phone": "ABSENT",
        }
