"""
BAD DECISION AI — Engine 2: Local Businesses — Brick & Mortar (Scrapling-First + DeepSeek Fallback)
==================================================================================================
This engine finds local businesses by scraping search
results and cross-referencing with OpenCorporates.

PIPELINE (as specified in TRD Section 4):
1. Scrapling fetches REAL data from Google Maps + search + OpenCorporates
2. DeepSeek structures the scraped HTML/text into clean lead objects
3. FILTER: Drop entities with > 500 employees or no physical address
4. ENRICHMENT: Multi-source contact finding:
   a. Scrapling fetches company website homepage
   b. If homepage fails → try /contact and /about pages
   c. If website scraping fails → DuckDuckGo search for contact details
   d. DeepSeek extracts contact info from whichever source succeeds
5. Validation gates run (DNS → Footprint → SMTP based on tier)
6. Dedup & cache

HARD RULE: Drop any entity with > 500 employees or lacking
local physical coordinates. We only want small-to-medium businesses.
"""

import json
from typing import List, Dict, Any

from scraping.stealth_fetcher import (
    stealth_fetch,
    stealth_fetch_contact_page,
    extract_text_from_html,
    build_google_maps_url,
    build_google_search_url,
    build_opencorporates_url,
    build_bing_search_url,
    build_duckduckgo_search_url,
    build_contact_search_url,
)
from ai.deepseek_middleware import execute_llm_payload, DEEPSEEK_SCOUT_MODEL
from validation.gate_dns import check_dns
from validation.gate_footprint import check_footprint
from validation.gate_smtp import check_smtp
from dedup.hash_dedup import compute_hash, check_duplicate


# Maximum number of leads to return per search
MAX_LEADS = 50

# Maximum employee count — we allow medium businesses too, not just tiny ones
MAX_EMPLOYEE_COUNT = 500


async def run_smb_maps(
    query: str,
    user_tier: str = "free",
) -> List[Dict[str, Any]]:
    """
    Find local brick-and-mortar businesses matching the user's query.

    PIPELINE:
    1. Scrapling fetches Google Maps + search + OpenCorporates
    2. DeepSeek structures scraped text into lead objects
    3. Filters: <500 employees, must have physical address
    4. Multi-source enrichment for contact details
    5. Validation gates run based on tier
    """

    leads = []

    # --------------------------------------------------------
    # PHASE 1: SCRAPLING — Fetch real data from the web
    # --------------------------------------------------------
    print(f"[SMB_MAPS] Scrapling Phase: Fetching local business data for '{query}'")

    scraped_texts = []

    # Source 1: Google Maps search
    maps_url = build_google_maps_url(query)
    maps_result = await stealth_fetch(maps_url)
    if maps_result:
        text = extract_text_from_html(maps_result["html"])
        if text:
            scraped_texts.append({"source": "Google Maps", "content": text})
            print(f"[SMB_MAPS] Scraped Google Maps: {len(text)} chars")
    else:
        print(f"[SMB_MAPS] Google Maps fetch failed — continuing with other sources")

    # Source 2: Google search for local businesses
    google_url = build_google_search_url(f"{query} local small business near me")
    google_result = await stealth_fetch(google_url)
    if google_result:
        text = extract_text_from_html(google_result["html"])
        if text:
            scraped_texts.append({"source": "Google Search", "content": text})
            print(f"[SMB_MAPS] Scraped Google Search: {len(text)} chars")
    else:
        print(f"[SMB_MAPS] Google Search fetch failed")

    # Source 3: OpenCorporates
    oc_url = build_opencorporates_url(query)
    oc_result = await stealth_fetch(oc_url)
    if oc_result:
        text = extract_text_from_html(oc_result["html"])
        if text:
            scraped_texts.append({"source": "OpenCorporates", "content": text})
            print(f"[SMB_MAPS] Scraped OpenCorporates: {len(text)} chars")
    else:
        print(f"[SMB_MAPS] OpenCorporates fetch failed")

    # Source 4: Bing search
    bing_url = build_bing_search_url(f"{query} local small business")
    bing_result = await stealth_fetch(bing_url)
    if bing_result:
        text = extract_text_from_html(bing_result["html"])
        if text:
            scraped_texts.append({"source": "Bing Search", "content": text})
            print(f"[SMB_MAPS] Scraped Bing Search: {len(text)} chars")
    else:
        print(f"[SMB_MAPS] Bing Search fetch failed")

    # Source 5: DuckDuckGo HTML
    ddg_url = build_duckduckgo_search_url(f"{query} local small business near me")
    ddg_result = await stealth_fetch(ddg_url)
    if ddg_result:
        text = extract_text_from_html(ddg_result["html"])
        if text:
            scraped_texts.append({"source": "DuckDuckGo", "content": text})
            print(f"[SMB_MAPS] Scraped DuckDuckGo: {len(text)} chars")
    else:
        print(f"[SMB_MAPS] DuckDuckGo fetch failed")

    # Combine all scraped content
    combined_text = ""
    if scraped_texts:
        combined_text = "\n\n".join(
            f"--- SOURCE: {s['source']} ---\n{s['content']}"
            for s in scraped_texts
        )
        print(f"[SMB_MAPS] Combined scraped text: {len(combined_text)} chars from {len(scraped_texts)} sources")
    else:
        print(f"[SMB_MAPS] All Scrapling sources returned empty text — using DeepSeek knowledge fallback")

    # --------------------------------------------------------
    # PHASE 2: DEEPSEEK — Structure the data
    # --------------------------------------------------------
    print(f"[SMB_MAPS] DeepSeek Phase: Structuring data")

    if combined_text:
        structure_prompt = f"""
        You are a local business data extractor. Below is REAL TEXT scraped from the internet
        about local businesses related to: "{query}"

        Your job is to extract REAL businesses mentioned in this text.
        Do NOT invent or hallucinate businesses that are not in the text.
        Only extract businesses that are clearly named in the scraped content.

        RULES:
        - Each business should have fewer than 500 employees (if mentioned)
        - Prefer businesses with a physical address
        - NO large corporations or chains

        SCRAPED CONTENT:
        {combined_text[:12000]}

        For each REAL business you find, provide:
        - company_name: The exact business name as mentioned
        - website_url: Their website (or "ABSENT" if not found)
        - address: Their physical street address if mentioned (or "ABSENT")
        - phone: Their phone number if mentioned (or "ABSENT")
        - employee_count: Approximate number of employees if mentioned (or "ABSENT")

        Return a JSON object with a "businesses" array. Find up to {MAX_LEADS} businesses.
        If you cannot find data for a field, write "ABSENT".

        Example format:
        {{
            "businesses": [
                {{
                    "company_name": "Mike's Roofing LLC",
                    "website_url": "https://mikesroofing.com",
                    "address": "123 Main St, Dallas, TX",
                    "phone": "+1-214-555-0123",
                    "employee_count": "8"
                }}
            ]
        }}
        """
    else:
        # Fallback: No scraped text — use DeepSeek's knowledge
        structure_prompt = f"""
        You are a local business data researcher. Find real small and medium businesses related to: "{query}"

        Use your knowledge to identify local businesses that match this search.
        Focus on businesses with fewer than 500 employees that have a physical location.

        RULES:
        - Each business should have fewer than 500 employees
        - Each business MUST have a physical location
        - NO large corporations or chains
        - Include as many REAL businesses as you can — aim for {MAX_LEADS}

        For each business you find, provide:
        - company_name: The real business name
        - website_url: Their website (or "ABSENT" if unknown)
        - address: Their physical street address (or "ABSENT" if unknown)
        - phone: Their phone number (or "ABSENT" if unknown)
        - employee_count: Approximate number of employees (or "ABSENT" if unknown)

        Return a JSON object with a "businesses" array. Find up to {MAX_LEADS} businesses.
        If you cannot find data for a field, write "ABSENT".

        Example format:
        {{
            "businesses": [
                {{
                    "company_name": "Mike's Roofing LLC",
                    "website_url": "https://mikesroofing.com",
                    "address": "123 Main St, Dallas, TX",
                    "phone": "+1-214-555-0123",
                    "employee_count": "8"
                }}
            ]
        }}
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
        print(f"[SMB_MAPS] DeepSeek structuring error: {e}")
        businesses = []

    print(f"[SMB_MAPS] DeepSeek extracted {len(businesses)} candidate businesses")

    # --------------------------------------------------------
    # PHASE 3: FILTER, VALIDATE & ENRICH each business
    # --------------------------------------------------------
    for biz in businesses[:MAX_LEADS]:
        company_name = biz.get("company_name", "ABSENT")
        website_url = biz.get("website_url", "ABSENT")
        address = biz.get("address", "ABSENT")
        employee_count = biz.get("employee_count", "ABSENT")
        biz_phone = biz.get("phone", "ABSENT")

        # HARD FILTER: Must have a company name
        if company_name == "ABSENT" or not company_name:
            continue

        # HARD FILTER: Must be < 500 employees
        try:
            if employee_count != "ABSENT" and int(str(employee_count).strip()) >= MAX_EMPLOYEE_COUNT:
                print(f"[SMB_MAPS] {company_name} has {employee_count} employees — DROPPED (>{MAX_EMPLOYEE_COUNT})")
                continue
        except (ValueError, TypeError):
            pass  # If we can't parse employee count, let it through

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

        # Enrich with DeepSeek using MULTI-SOURCE strategy
        enrichment = await _enrich_local_lead(
            company_name, website_url, address, biz_phone, user_tier
        )

        # Use enrichment data but prefer data from structure phase if available
        # (structure phase sometimes has phone from scraped text)
        final_phone = enrichment.get("phone", "ABSENT")
        if biz_phone != "ABSENT" and final_phone == "ABSENT":
            final_phone = biz_phone

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
            "phone": final_phone,
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
    known_phone: str,
    user_tier: str,
) -> Dict[str, str]:
    """
    Enrich a local business lead with DM details.
    Uses a MULTI-SOURCE strategy to find real contact info:

    1. Try scraping the company website homepage
    2. If homepage returns 0 chars → try /contact and /about pages
    3. If website scraping fails entirely → DuckDuckGo search for contact details
    4. DeepSeek extracts structured contact info from whichever source succeeds

    This ensures we get REAL contact data from verified sources,
    not hallucinated or guessed information.
    """
    scraped_text = ""
    source_description = ""

    # STRATEGY 1: Try scraping the company homepage
    if website_url and website_url != "ABSENT":
        print(f"[SMB_MAPS] Scrapling: Fetching company website {website_url}")
        site_result = await stealth_fetch(website_url)
        if site_result:
            scraped_text = extract_text_from_html(site_result["html"], max_chars=8000)
            if scraped_text:
                print(f"[SMB_MAPS] Scraped company website: {len(scraped_text)} chars")
                source_description = f"the website of {company_name} ({website_url})"

    # STRATEGY 2: If homepage returned 0 chars, try contact/about pages
    if not scraped_text and website_url and website_url != "ABSENT":
        print(f"[SMB_MAPS] Homepage returned empty — trying contact/about pages for {website_url}")
        contact_result = await stealth_fetch_contact_page(website_url)
        if contact_result:
            scraped_text = extract_text_from_html(contact_result["html"], max_chars=8000)
            if scraped_text:
                print(f"[SMB_MAPS] Scraped contact page: {len(scraped_text)} chars")
                source_description = f"the contact/about page of {company_name} ({website_url})"

    # STRATEGY 3: If website scraping failed entirely, do a web search for contact details
    if not scraped_text:
        print(f"[SMB_MAPS] Website scraping failed — searching DuckDuckGo for contact details of {company_name}")
        search_url = build_contact_search_url(company_name)
        search_result = await stealth_fetch(search_url)
        if search_result:
            scraped_text = extract_text_from_html(search_result["html"], max_chars=8000)
            if scraped_text:
                print(f"[SMB_MAPS] Found contact info via web search: {len(scraped_text)} chars")
                source_description = f"web search results for '{company_name}' contact details"

    # Build the enrichment prompt based on what data source we have
    if scraped_text:
        prompt = f"""
        You are a business contact researcher. Below is REAL TEXT from {source_description}
        for the business: "{company_name}" located at "{address}"

        Extract the following contact information from this text:
        - dm_name: Full name of the owner, CEO, managing director, or founder
        - dm_position: Their job title
        - verified_email: Their work or business email address
        - linkedin: Their LinkedIn profile URL (or the company LinkedIn page)
        - instagram: The business Instagram URL
        - phone: The business phone number

        IMPORTANT RULES:
        - Only extract information that is clearly present in the text
        - Do NOT guess, infer, or generate email addresses using patterns (like first.last@domain)
        - If a phone number is in the text, include it exactly as written
        - If you cannot find any piece of information, write "ABSENT"

        SOURCE TEXT:
        {scraped_text[:8000]}

        Return a single JSON object.
        """
    else:
        # All scraping failed — use DeepSeek knowledge as last resort
        # But be very explicit about not guessing
        prompt = f"""
        You are a business contact researcher. Find verified contact details for:
        "{company_name}" located at "{address}" ({website_url})

        Look for:
        - dm_name: Full name of the owner, CEO, managing director, or founder
        - dm_position: Their job title
        - verified_email: Their actual work email (only if you are CERTAIN it is real — do NOT guess using patterns like first.last@domain)
        - linkedin: Their LinkedIn profile URL (only if you are CERTAIN)
        - instagram: The business Instagram URL
        - phone: The business phone number (only if you are CERTAIN it is real)

        CRITICAL RULES:
        - Only provide information you are CONFIDENT is accurate from your training data
        - Do NOT generate email addresses using common patterns (like info@, hello@, contact@)
        - Do NOT guess phone numbers
        - It is better to return "ABSENT" than to provide wrong information
        - If the company is real but you don't know their contact details, write "ABSENT"

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
        print(f"[SMB_MAPS] Enrichment error for {company_name}: {e}")
        return {
            "dm_name": "ABSENT",
            "dm_position": "ABSENT",
            "verified_email": "ABSENT",
            "linkedin": "ABSENT",
            "instagram": "ABSENT",
            "phone": "ABSENT",
        }
