"""
BAD DECISION AI — Engine 1: Digital Ads Intelligence (API-First)
================================================================
PIPELINE:
1. Serper.dev → Google search for businesses running ads
2. Serper.dev → Search Meta Ads Library pages
3. Dedup
4. Enrich each lead (same pipeline as smb_maps)
5. Validation gates

V3: Accepts location dict for geo-targeted Serper searches.
"""

import asyncio
import json
import re
from typing import List, Dict, Any

from api_clients.serper import serper_search, serper_site_search
from enrichment.email_finder import find_emails_for_domain
from scraping.stealth_fetcher import stealth_fetch, extract_text_from_html, is_js_shell
from ai.deepseek_middleware import execute_llm_payload, DEEPSEEK_SCOUT_MODEL
from validation.gate_dns import check_dns
from validation.gate_footprint import check_footprint
from validation.gate_smtp import check_smtp
from dedup.hash_dedup import compute_hash, check_duplicate


async def run_ads_intent(query: str, user_tier: str = "free", location: dict = None) -> List[Dict[str, Any]]:
    leads = []

    # Default location
    if location is None:
        location = {}

    # PHASE 1: DISCOVERY (parallel)
    print(f"[ADS_INTENT] Discovery phase: '{query}' (location: {location})")

    google_task = serper_search(f"{query} advertising running ads", num_results=20, location=location)
    meta_task = serper_site_search("facebook.com", f"{query} ads", num_results=10, location=location)
    meta_ads_task = serper_site_search("facebook.com/ads/library", query, num_results=10, location=location)

    google_results, meta_results, meta_ads_results = await asyncio.gather(
        google_task, meta_task, meta_ads_task, return_exceptions=True
    )

    google_results = google_results if isinstance(google_results, list) else []
    meta_results = meta_results if isinstance(meta_results, list) else []
    meta_ads_results = meta_ads_results if isinstance(meta_ads_results, list) else []

    print(f"[ADS_INTENT] Google: {len(google_results)} | Meta: {len(meta_results)} | Ads Library: {len(meta_ads_results)}")

    # Extract business names and URLs from search results
    candidates = []
    seen_names = set()

    for result in google_results + meta_results + meta_ads_results:
        title = result.get("title", "")
        link = result.get("link", "")
        snippet = result.get("snippet", "")

        # Skip social/profile URLs — we want actual business websites
        if any(skip in link for skip in ["facebook.com/ads/library", "pinterest.com", "twitter.com"]):
            # But this IS an ad listing — extract business info
            if title and title not in seen_names:
                seen_names.add(title)
                candidates.append({
                    "company_name": title.split(" - ")[0].split(" | ")[0].strip(),
                    "website_url": "ABSENT",
                    "ad_snippet": snippet,
                    "ad_platform": "Meta Ads" if "facebook" in link.lower() else "Google Ads",
                })
            continue

        # Extract domain from URL
        domain = re.sub(r'^https?://(www\.)?', '', link).split('/')[0]

        if title and domain and title not in seen_names:
            seen_names.add(title)
            candidates.append({
                "company_name": title.split(" - ")[0].split(" | ")[0].strip(),
                "website_url": f"https://{domain}" if domain else "ABSENT",
                "ad_snippet": snippet,
                "ad_platform": "Unknown",
            })

    print(f"[ADS_INTENT] {len(candidates)} unique ad-spending businesses")

    # PHASE 2: ENRICH (parallel batches)
    BATCH_SIZE = 5
    for i in range(0, min(len(candidates), 50), BATCH_SIZE):
        batch = candidates[i:i + BATCH_SIZE]
        tasks = [_enrich_ads_lead(biz, user_tier) for biz in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, dict) and result.get('company_name'):
                leads.append(result)

    print(f"[ADS_INTENT] Completed: {len(leads)} enriched leads")
    return leads


async def _enrich_ads_lead(biz: Dict[str, Any], user_tier: str) -> Dict[str, Any]:
    company_name = biz.get("company_name", "ABSENT")
    website_url = biz.get("website_url", "ABSENT")
    ad_snippet = biz.get("ad_snippet", "")
    ad_platform = biz.get("ad_platform", "Unknown")

    if company_name == "ABSENT":
        return {}

    url_to_hash = website_url if website_url != "ABSENT" else company_name
    domain_hash = compute_hash(url_to_hash)

    is_dup, cached_data = await check_duplicate(domain_hash)
    if is_dup and cached_data:
        return cached_data

    # DNS check
    if website_url and website_url != "ABSENT":
        dns_ok = await check_dns(website_url)
        if not dns_ok:
            return {}

    # Email enrichment
    verified_email = "ABSENT"
    email_source = "ABSENT"
    dm_name = "ABSENT"
    dm_position = "ABSENT"

    if website_url and website_url != "ABSENT":
        domain = re.sub(r'^https?://', '', website_url.lower())
        domain = re.sub(r'/.*$', '', domain).strip('/')

        email_result = await find_emails_for_domain(domain)
        verified_email = email_result.get("verified_email", "ABSENT")
        email_source = email_result.get("email_source", "ABSENT")

        # Try to get DM name from website
        result = await stealth_fetch(f"{website_url.rstrip('/')}/about")
        if result and result.get("html") and not is_js_shell(result["html"]):
            text = extract_text_from_html(result["html"], max_chars=3000)
            if text:
                try:
                    response = await execute_llm_payload({
                        "model": DEEPSEEK_SCOUT_MODEL,
                        "messages": [
                            {"role": "system", "content": "Extract owner/CEO name and title. Return JSON: {\"dm_name\": \"...\", \"dm_position\": \"...\"}. ABSENT if not found."},
                            {"role": "user", "content": text[:2000]},
                        ],
                        "response_format": {"type": "json_object"},
                        "temperature": 0.1,
                    })
                    content = response.get("choices", [{}])[0].get("message", {}).get("content", "{}")
                    parsed = json.loads(content)
                    dm_name = parsed.get("dm_name", "ABSENT")
                    dm_position = parsed.get("dm_position", "ABSENT")
                except Exception:
                    pass

    lead = {
        "domain_hash": domain_hash,
        "company_name": company_name,
        "website_url": website_url,
        "phone": "ABSENT",
        "verified_email": verified_email,
        "dm_name": dm_name,
        "dm_position": dm_position,
        "engine_type": "ads_intent",
        "engine_data": {
            "ad_platform": ad_platform,
            "ad_status": "active",
            "ad_creative_snippet": ad_snippet[:200] if ad_snippet else "ABSENT",
            "ad_spend_signal": "medium",  # Default — can be enhanced later
        },
        "discovery_source": "serper",
        "email_source": email_source,
    }

    # Validation gates
    if user_tier in ("starter", "growth", "pro"):
        if not check_footprint(lead):
            return {}

    if user_tier == "pro" and verified_email != "ABSENT":
        smtp_ok, is_catchall = await check_smtp(verified_email)
        lead["engine_data"]["is_catchall"] = is_catchall
        if not smtp_ok and not is_catchall:
            return {}

    return lead
