"""
BAD DECISION AI — Engine 2: Local Business Finder (SMB Maps)
=============================================================
Finds local brick-and-mortar businesses using Overpass API
(OpenStreetMap), Serper.dev Maps, and Scrapling.
Hard filter: <50 employees, must have physical address.
"""

import json
from typing import List, Dict, Any
from scraping.stealth_fetcher import (
    stealth_fetch, extract_text_from_html, extract_emails_from_html,
    build_google_maps_url, build_google_search_url,
)
from ai.deepseek_middleware import execute_llm_payload, DEEPSEEK_SCOUT_MODEL
from validation.gate_dns import check_dns
from validation.gate_footprint import check_footprint
from validation.gate_smtp import check_smtp
from dedup.hash_dedup import compute_hash, check_duplicate


async def run_smb_maps(query: str, user_tier: str = "free", location: str = "") -> List[Dict[str, Any]]:
    """Find local businesses matching the query."""
    leads = []

    # PHASE 1: FETCH DATA from multiple sources
    print(f"[SMB_MAPS] Fetching local business data for '{query}' in '{location}'")
    scraped_texts = []
    overpass_businesses = []

    # Source 1: Overpass API (free, unlimited OpenStreetMap data)
    try:
        from api_clients.overpass import search_businesses
        overpass_businesses = await search_businesses(query, location)
        if overpass_businesses:
            op_text = "\n".join(
                f"Name: {b.get('name', 'N/A')} | Address: {b.get('address', 'N/A')} | "
                f"Website: {b.get('website', 'N/A')} | Phone: {b.get('phone', 'N/A')} | "
                f"Type: {b.get('type', 'N/A')}"
                for b in overpass_businesses[:30]
            )
            scraped_texts.append({"source": "Overpass API (OpenStreetMap)", "content": op_text})
            print(f"[SMB_MAPS] Overpass: {len(overpass_businesses)} businesses")
    except Exception as e:
        print(f"[SMB_MAPS] Overpass error: {e}")

    # Source 2: Serper.dev Maps
    try:
        from api_clients.serper import serper_maps_search
        serper_maps = await serper_maps_search(query, location)
        if serper_maps:
            maps_text = "\n".join(
                f"Name: {r.get('title', '')} | Address: {r.get('address', '')} | "
                f"Phone: {r.get('phone', '')} | Website: {r.get('website', '')}"
                for r in serper_maps[:20]
            )
            scraped_texts.append({"source": "Serper Maps", "content": maps_text})
            print(f"[SMB_MAPS] Serper Maps: {len(serper_maps)} results")
    except Exception as e:
        print(f"[SMB_MAPS] Serper Maps error: {e}")

    # Source 3: Scrapling fallback (Google Maps + Google Search)
    if len(scraped_texts) < 2:
        maps_url = build_google_maps_url(query, location)
        maps_result = await stealth_fetch(maps_url)
        if maps_result:
            text = extract_text_from_html(maps_result["html"])
            if text:
                scraped_texts.append({"source": "Google Maps", "content": text})

        google_url = build_google_search_url(f"{query} local small business near {location}")
        google_result = await stealth_fetch(google_url)
        if google_result:
            text = extract_text_from_html(google_result["html"])
            if text:
                scraped_texts.append({"source": "Google Search", "content": text})

    if not scraped_texts:
        print(f"[SMB_MAPS] All sources failed")
        return []

    combined_text = "\n\n".join(f"--- SOURCE: {s['source']} ---\n{s['content']}" for s in scraped_texts)

    # PHASE 2: DEEPSEEK — Structure the data
    print(f"[SMB_MAPS] DeepSeek structuring")
    structure_prompt = f"""
    You are a local business data extractor. Below is REAL TEXT about local businesses related to: "{query}" in "{location}"

    Extract REAL businesses mentioned. Do NOT invent businesses.
    Only extract clearly named businesses from the text.

    HARD RULES:
    - Business MUST have a physical address (no online-only)
    - Business MUST be <50 employees (no chains or large corps)

    SCRAPED CONTENT:
    {combined_text[:12000]}

    For each business, provide:
    - company_name: Exact name
    - website_url: Website (or "ABSENT")
    - address: Physical address (or "ABSENT")
    - phone: Phone number (or "ABSENT")

    Return JSON with "businesses" array. Up to 25 businesses.
    """

    try:
        response = await execute_llm_payload({
            "model": DEEPSEEK_SCOUT_MODEL,
            "messages": [
                {"role": "system", "content": "Precise data extractor. Only extract REAL businesses from text. Never invent. Valid JSON. 'ABSENT' for missing."},
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
        print(f"[SMB_MAPS] DeepSeek error: {e}")
        businesses = []

    # If Overpass gave us structured data, prefer it
    if overpass_businesses and not businesses:
        businesses = overpass_businesses

    print(f"[SMB_MAPS] {len(businesses)} candidates found")

    # PHASE 3: FILTER, VALIDATE & ENRICH
    for biz in businesses[:25]:
        company_name = biz.get("company_name", biz.get("name", "ABSENT"))
        website_url = biz.get("website_url", biz.get("website", "ABSENT"))
        address = biz.get("address", "ABSENT")
        phone = biz.get("phone", "ABSENT")

        if company_name == "ABSENT" or not company_name:
            continue

        # Must have physical address
        if address == "ABSENT" or not address:
            print(f"[SMB_MAPS] {company_name} — no address, DROPPED")
            continue

        url_to_hash = website_url if website_url != "ABSENT" else company_name
        domain_hash = compute_hash(url_to_hash)

        is_dup, cached_data = await check_duplicate(domain_hash)
        if is_dup and cached_data:
            leads.append(cached_data)
            continue

        # Gate 1: DNS
        if website_url != "ABSENT":
            dns_ok = await check_dns(website_url)
            if not dns_ok:
                continue

        # Enrich
        enrichment = await _enrich_local_lead(company_name, website_url, address, user_tier)

        # Use scraped phone if enrichment didn't find one
        final_phone = enrichment.get("phone", "ABSENT")
        if (final_phone == "ABSENT" or not final_phone) and phone and phone != "ABSENT":
            final_phone = phone

        lead = {
            "domain_hash": domain_hash,
            "company_name": company_name,
            "website_url": website_url,
            "dm_name": enrichment.get("dm_name", "ABSENT"),
            "dm_position": enrichment.get("dm_position", "ABSENT"),
            "verified_email": enrichment.get("verified_email", "ABSENT"),
            "email_source": enrichment.get("email_source", "ABSENT"),
            "is_catchall": False,
            "linkedin": enrichment.get("linkedin", "ABSENT"),
            "instagram": enrichment.get("instagram", "ABSENT"),
            "phone": final_phone,
            "address": address,
        }

        # Gate 2: Footprint (Starter+)
        if user_tier in ("starter", "growth", "pro"):
            if not check_footprint(lead):
                continue

        # Gate 3: SMTP (Pro only)
        if user_tier == "pro" and lead["verified_email"] != "ABSENT":
            smtp_ok, is_catchall = await check_smtp(lead["verified_email"])
            lead["is_catchall"] = is_catchall
            if not smtp_ok and not is_catchall:
                continue

        leads.append(lead)

    return leads


async def _enrich_local_lead(company_name: str, website_url: str, address: str, user_tier: str) -> Dict[str, str]:
    """Enrich a local business lead with DM details."""
    scraped_website_text = ""
    scraped_emails = []

    if website_url and website_url != "ABSENT":
        site_result = await stealth_fetch(website_url)
        if site_result:
            scraped_website_text = extract_text_from_html(site_result["html"], max_chars=8000)
            scraped_emails = extract_emails_from_html(site_result["html"])

    # Try Hunter.io
    hunter_email = ""
    try:
        from api_clients.hunter_client import find_email
        domain = website_url.replace("https://", "").replace("http://", "").split("/")[0] if website_url != "ABSENT" else ""
        if domain:
            hunter_email, _ = await find_email(domain, company_name)
    except Exception:
        pass

    email_hint = ""
    if scraped_emails:
        email_hint = f"\nEMAILS FOUND: {', '.join(scraped_emails[:5])}"
    if hunter_email:
        email_hint += f"\nHUNTER.IO: {hunter_email}"

    if scraped_website_text:
        prompt = f"""
        Extract contact info from "{company_name}" at "{address}" ({website_url}):
        {email_hint}

        SCRAPED CONTENT:
        {scraped_website_text[:8000]}

        Return JSON: dm_name, dm_position, verified_email, email_source, linkedin, instagram, phone.
        Use "ABSENT" for missing.
        """
    else:
        prompt = f"""
        Find the owner/decision maker for: "{company_name}" at "{address}" ({website_url})
        {email_hint}
        Return JSON: dm_name, dm_position, verified_email, email_source, linkedin, instagram, phone.
        Use "ABSENT" for missing.
        """

    try:
        response = await execute_llm_payload({
            "model": DEEPSEEK_SCOUT_MODEL,
            "messages": [
                {"role": "system", "content": "Precise data extractor. Only extract from text. Never invent. Valid JSON. 'ABSENT' for missing."},
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        })
        content = response.get("choices", [{}])[0].get("message", {}).get("content", "{}")
        return json.loads(content)
    except Exception as e:
        print(f"[SMB_MAPS] Enrichment error: {e}")
        result = {
            "dm_name": "ABSENT", "dm_position": "ABSENT", "verified_email": "ABSENT",
            "email_source": "ABSENT", "linkedin": "ABSENT", "instagram": "ABSENT", "phone": "ABSENT",
        }
        if hunter_email:
            result["verified_email"] = hunter_email
            result["email_source"] = "hunter_io"
        elif scraped_emails:
            result["verified_email"] = scraped_emails[0]
            result["email_source"] = "website_regex"
        return result
