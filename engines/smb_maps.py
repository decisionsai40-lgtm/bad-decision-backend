"""
BAD DECISION AI — Engine 2: Local Businesses (API-First + Scrapling)
=====================================================================
PIPELINE:
1. Overpass API → Find businesses by category/location (FREE, unlimited)
2. Serper.dev Maps → Google Maps structured data (2,500 free credits)
3. Dedup + filter (remove duplicates, filter <50 employees)
4. Enrich each lead:
   a. Scrapling (FIXED) → Scrape website /contact page for emails/phones
   b. Email regex extraction from HTML (instant)
   c. Email pattern prediction + SMTP verification
   d. Hunter.io domain search (25/month free)
   e. Firecrawl for JS-rendered sites (1K credits free)
5. Validation gates (DNS → Footprint → SMTP)

V3: Accepts location dict for geo-targeted Overpass and Serper searches.
"""

import asyncio
import json
import re
from typing import List, Dict, Any

from api_clients.overpass import search_businesses
from api_clients.serper import serper_maps, serper_search
from enrichment.email_finder import find_emails_for_domain
from scraping.stealth_fetcher import (
    stealth_fetch, extract_text_from_html, extract_emails_from_html,
    extract_phones_from_html, is_js_shell, extract_json_ld_data,
)
from ai.deepseek_middleware import execute_llm_payload, DEEPSEEK_SCOUT_MODEL
from validation.gate_dns import check_dns
from validation.gate_footprint import check_footprint
from validation.gate_smtp import check_smtp
from dedup.hash_dedup import compute_hash, check_duplicate


async def run_smb_maps(query: str, user_tier: str = "free", location: dict = None) -> List[Dict[str, Any]]:
    leads = []

    # Default location
    if location is None:
        location = {}

    # PHASE 1: DISCOVERY (parallel — 3 sources at once)
    print(f"[SMB_MAPS] Discovery phase: '{query}' (location: {location})")

    overpass_task = search_businesses(query, location_dict=location)
    serper_task = serper_maps(query, location=location)

    overpass_results, serper_results = await asyncio.gather(
        overpass_task, serper_task, return_exceptions=True
    )

    overpass_businesses = overpass_results if isinstance(overpass_results, list) else []
    serper_businesses = serper_results if isinstance(serper_results, list) else []

    print(f"[SMB_MAPS] Overpass: {len(overpass_businesses)} | Serper: {len(serper_businesses)}")

    # Merge and deduplicate by name+address
    all_businesses = []
    seen_keys = set()

    for biz in overpass_businesses + serper_businesses:
        key = f"{biz.get('company_name', '').lower()}_{biz.get('address', '').lower()[:30]}"
        if key not in seen_keys and biz.get('company_name'):
            seen_keys.add(key)
            all_businesses.append(biz)

    print(f"[SMB_MAPS] Merged: {len(all_businesses)} unique businesses")

    # PHASE 2: ENRICH (parallel batches of 5)
    BATCH_SIZE = 5
    for i in range(0, min(len(all_businesses), 100), BATCH_SIZE):
        batch = all_businesses[i:i + BATCH_SIZE]
        tasks = [_enrich_smb_lead(biz, user_tier) for biz in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, dict) and result.get('company_name'):
                leads.append(result)

    print(f"[SMB_MAPS] Completed: {len(leads)} enriched leads")
    return leads


async def _enrich_smb_lead(biz: Dict[str, Any], user_tier: str) -> Dict[str, Any]:
    """Enrich a single SMB lead with contact details."""
    company_name = biz.get("company_name", "ABSENT")
    website = biz.get("website", "ABSENT")
    phone = biz.get("phone", "ABSENT")
    address = biz.get("address", "ABSENT")
    email_from_discovery = biz.get("email", "ABSENT")

    # Dedup check
    url_to_hash = website if website not in ("ABSENT", "NONE", "") else company_name
    domain_hash = compute_hash(url_to_hash)

    is_dup, cached_data = await check_duplicate(domain_hash)
    if is_dup and cached_data:
        return cached_data

    # DNS check for website
    if website and website not in ("ABSENT", "NONE", ""):
        dns_ok = await check_dns(website)
        if not dns_ok:
            print(f"[SMB_MAPS] DNS failed for {website} — DROPPED")
            return {}

    # Enrich with email finder pipeline
    dm_name = "ABSENT"
    dm_position = "ABSENT"
    verified_email = "ABSENT"
    email_source = "ABSENT"
    email_pattern = "ABSENT"

    if website and website not in ("ABSENT", "NONE", ""):
        domain = website.lower().strip()
        domain = re.sub(r'^https?://', '', domain)
        domain = re.sub(r'/.*$', '', domain).strip('/')

        # Try to scrape website for DM name + contact info
        dm_name, dm_position = await _extract_dm_from_website(website)

        # Find emails using the multi-strategy pipeline
        first_name = dm_name.split()[0] if dm_name != "ABSENT" and ' ' in dm_name else ""
        last_name = dm_name.split()[-1] if dm_name != "ABSENT" and ' ' in dm_name else ""

        email_result = await find_emails_for_domain(domain, first_name, last_name)

        verified_email = email_result.get("verified_email", "ABSENT")
        email_source = email_result.get("email_source", "ABSENT")
        email_pattern = email_result.get("email_pattern", "ABSENT")

    # Use discovery email if enrichment didn't find one
    if verified_email == "ABSENT" and email_from_discovery and email_from_discovery != "ABSENT":
        verified_email = email_from_discovery
        email_source = "overpass"

    lead = {
        "domain_hash": domain_hash,
        "company_name": company_name,
        "website_url": website if website != "NONE" else "ABSENT",
        "phone": phone,
        "verified_email": verified_email,
        "dm_name": dm_name,
        "dm_position": dm_position,
        "engine_type": "smb_maps",
        "engine_data": {
            "city": biz.get("city", "ABSENT"),
            "postcode": biz.get("postcode", "ABSENT"),
            "latitude": biz.get("latitude"),
            "longitude": biz.get("longitude"),
            "category": biz.get("category", "ABSENT"),
            "rating": biz.get("rating"),
            "review_count": biz.get("review_count") or biz.get("reviews"),
            "business_hours": biz.get("opening_hours", "ABSENT"),
            "email_pattern": email_pattern,
        },
        "discovery_source": biz.get("discovery_source", "unknown"),
        "email_source": email_source,
    }

    # Validation gates
    if user_tier in ("starter", "growth", "pro"):
        if not check_footprint(lead):
            print(f"[SMB_MAPS] Footprint failed for {company_name} — DROPPED")
            return {}

    if user_tier == "pro" and verified_email != "ABSENT":
        smtp_ok, is_catchall = await check_smtp(verified_email)
        lead["engine_data"]["is_catchall"] = is_catchall
        if not smtp_ok and not is_catchall:
            print(f"[SMB_MAPS] SMTP failed for {verified_email} — DROPPED")
            return {}

    return lead


async def _extract_dm_from_website(website_url: str) -> tuple:
    """Try to extract decision maker name from website."""
    for page in ["/about", "/team", "/about-us"]:
        url = f"{website_url.rstrip('/')}{page}"
        result = await stealth_fetch(url)
        if result and result.get("html") and not is_js_shell(result["html"]):
            text = extract_text_from_html(result["html"], max_chars=5000)
            if text:
                # Use DeepSeek to extract DM from real scraped text
                try:
                    response = await execute_llm_payload({
                        "model": DEEPSEEK_SCOUT_MODEL,
                        "messages": [
                            {"role": "system", "content": "Extract the owner, CEO, or founder name and title from this text. Return JSON: {\"dm_name\": \"...\", \"dm_position\": \"...\"}. Use ABSENT if not found."},
                            {"role": "user", "content": text[:3000]},
                        ],
                        "response_format": {"type": "json_object"},
                        "temperature": 0.1,
                    })
                    content = response.get("choices", [{}])[0].get("message", {}).get("content", "{}")
                    parsed = json.loads(content)
                    name = parsed.get("dm_name", "ABSENT")
                    position = parsed.get("dm_position", "ABSENT")
                    if name and name != "ABSENT":
                        return name, position
                except Exception:
                    pass
    return "ABSENT", "ABSENT"
