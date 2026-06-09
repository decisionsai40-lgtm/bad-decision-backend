"""
BAD DECISION AI — Engine 3: Web-Absent Businesses (API-First)
=============================================================
PIPELINE:
1. Overpass API → Find businesses WITHOUT website tag
2. Serper.dev → Search for businesses on aggregator sites
3. Dedup
4. Enrich each lead with emails (aggregator profile scrape + web search)
5. NO DNS gate (they don't have websites)

V3: Accepts location dict for geo-targeted Overpass and Serper searches.
"""

import asyncio
import json
import re
from typing import List, Dict, Any

from api_clients.overpass import search_businesses_without_website
from api_clients.serper import serper_search, serper_site_search
from enrichment.email_finder import find_email_for_web_absent
from scraping.stealth_fetcher import stealth_fetch, extract_text_from_html, extract_emails_from_html, is_js_shell, extract_phones_from_html
from ai.deepseek_middleware import execute_llm_payload, DEEPSEEK_SCOUT_MODEL
from validation.gate_footprint import check_footprint
from validation.gate_smtp import check_smtp
from dedup.hash_dedup import compute_hash, check_duplicate


async def run_web_absent(query: str, user_tier: str = "free", location: dict = None) -> List[Dict[str, Any]]:
    leads = []

    # Default location
    if location is None:
        location = {}

    # PHASE 1: DISCOVERY (parallel)
    print(f"[WEB_ABSENT] Discovery phase: '{query}' (location: {location})")

    overpass_task = search_businesses_without_website(query, location_dict=location)
    yelp_task = serper_site_search("yelp.com", query, num_results=10, location=location)
    houzz_task = serper_site_search("houzz.com", query, num_results=10, location=location)

    overpass_results, yelp_results, houzz_results = await asyncio.gather(
        overpass_task, yelp_task, houzz_task, return_exceptions=True
    )

    overpass_businesses = overpass_results if isinstance(overpass_results, list) else []
    yelp_results = yelp_results if isinstance(yelp_results, list) else []
    houzz_results = houzz_results if isinstance(houzz_results, list) else []

    print(f"[WEB_ABSENT] Overpass: {len(overpass_businesses)} | Yelp: {len(yelp_results)} | Houzz: {len(houzz_results)}")

    # Process Overpass results (already structured)
    all_businesses = []
    seen_names = set()

    for biz in overpass_businesses:
        name = biz.get("company_name", "")
        if name and name.lower() not in seen_names:
            seen_names.add(name.lower())
            all_businesses.append({
                "company_name": name,
                "phone": biz.get("phone", "ABSENT"),
                "email": biz.get("email", "ABSENT"),
                "address": biz.get("address", "ABSENT"),
                "city": biz.get("city", "ABSENT"),
                "postcode": biz.get("postcode", "ABSENT"),
                "latitude": biz.get("latitude"),
                "longitude": biz.get("longitude"),
                "category": biz.get("category", "ABSENT"),
                "aggregator_source": "OpenStreetMap",
                "aggregator_url": "ABSENT",
                "website": "NONE",
                "discovery_source": "overpass",
            })

    # Process aggregator search results (Yelp/Houzz)
    for result in yelp_results + houzz_results:
        title = result.get("title", "").split(" - ")[0].strip()
        link = result.get("link", "")
        snippet = result.get("snippet", "")

        source = "Yelp" if "yelp" in link.lower() else "Houzz" if "houzz" in link.lower() else "Aggregator"

        if title and title.lower() not in seen_names:
            seen_names.add(title.lower())
            all_businesses.append({
                "company_name": title,
                "phone": "ABSENT",
                "email": "ABSENT",
                "address": "ABSENT",
                "city": "ABSENT",
                "postcode": "ABSENT",
                "latitude": None,
                "longitude": None,
                "category": "ABSENT",
                "aggregator_source": source,
                "aggregator_url": link,
                "website": "NONE",
                "discovery_source": "serper",
            })

    print(f"[WEB_ABSENT] {len(all_businesses)} unique businesses without websites")

    # PHASE 2: ENRICH (parallel batches)
    BATCH_SIZE = 5
    for i in range(0, min(len(all_businesses), 100), BATCH_SIZE):
        batch = all_businesses[i:i + BATCH_SIZE]
        tasks = [_enrich_web_absent_lead(biz, user_tier) for biz in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, dict) and result.get('company_name'):
                leads.append(result)

    print(f"[WEB_ABSENT] Completed: {len(leads)} enriched leads")
    return leads


async def _enrich_web_absent_lead(biz: Dict[str, Any], user_tier: str) -> Dict[str, Any]:
    company_name = biz.get("company_name", "ABSENT")
    phone = biz.get("phone", "ABSENT")
    address = biz.get("address", "ABSENT")
    email_from_discovery = biz.get("email", "ABSENT")
    aggregator_source = biz.get("aggregator_source", "ABSENT")
    aggregator_url = biz.get("aggregator_url", "ABSENT")

    if company_name == "ABSENT":
        return {}

    url_to_hash = aggregator_url if aggregator_url != "ABSENT" else company_name
    domain_hash = compute_hash(url_to_hash)

    is_dup, cached_data = await check_duplicate(domain_hash)
    if is_dup and cached_data:
        return cached_data

    # Find email using web_absent-specific strategies
    location = biz.get("city", "")
    email_result = await find_email_for_web_absent(
        company_name=company_name,
        aggregator_url=aggregator_url,
        location=location,
    )

    verified_email = email_result.get("verified_email", "ABSENT")
    email_source = email_result.get("email_source", "ABSENT")

    # Use discovery email if enrichment didn't find one
    if verified_email == "ABSENT" and email_from_discovery and email_from_discovery != "ABSENT":
        verified_email = email_from_discovery
        email_source = "overpass"

    # Try to extract phone from aggregator profile if not already have it
    if phone == "ABSENT" and aggregator_url != "ABSENT":
        result = await stealth_fetch(aggregator_url)
        if result and result.get("html") and not is_js_shell(result["html"]):
            phones = extract_phones_from_html(result["html"])
            if phones:
                phone = phones[0]

    # Determine digital presence score
    has_email = verified_email != "ABSENT"
    has_phone = phone != "ABSENT"

    if has_email and has_phone:
        digital_score = "minimal"
    elif has_phone:
        digital_score = "weak"
    else:
        digital_score = "none"

    # Determine missing services
    missing = ["website"]
    if not has_email:
        missing.append("email_on_own_domain")
    missing.append("online_booking")

    lead = {
        "domain_hash": domain_hash,
        "company_name": company_name,
        "website_url": "NONE",
        "phone": phone,
        "verified_email": verified_email,
        "dm_name": "ABSENT",
        "dm_position": "ABSENT",
        "engine_type": "web_absent",
        "engine_data": {
            "aggregator_source": aggregator_source,
            "aggregator_url": aggregator_url,
            "digital_presence_score": digital_score,
            "missing_services": missing,
            "opportunity": "No website found — needs web design/development",
            "city": biz.get("city", "ABSENT"),
            "postcode": biz.get("postcode", "ABSENT"),
            "latitude": biz.get("latitude"),
            "longitude": biz.get("longitude"),
            "category": biz.get("category", "ABSENT"),
        },
        "discovery_source": biz.get("discovery_source", "unknown"),
        "email_source": email_source,
    }

    # Validation gates (skip DNS — no website)
    if user_tier in ("starter", "growth", "pro"):
        if not check_footprint(lead):
            return {}

    if user_tier == "pro" and verified_email != "ABSENT":
        smtp_ok, is_catchall = await check_smtp(verified_email)
        lead["engine_data"]["is_catchall"] = is_catchall
        if not smtp_ok and not is_catchall:
            return {}

    return lead
