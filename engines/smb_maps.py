"""
BAD DECISION — Engine 1: smb_maps (Local Businesses)
=====================================================
This engine finds local brick-and-mortar businesses using:
  1. Serper maps search (PRIMARY — structured data, fast)
  2. 10x concurrent Serper web searches (build_smb_maps_queries)
  3. OSM Overpass (FALLBACK ONLY — if Serper returns too few results)
  4. ScrapingAnt for Yelp pages (if Serper found Yelp results)

PIPELINE:
  1. Serper maps search (primary — structured data, bypasses DeepSeek structuring)
  2. 10x Serper web searches concurrently (text results → DeepSeek structuring)
  3. If total results < lead_target: try OSM as fallback
  4. DeepSeek structures any remaining text results (NOT the maps results)
  5. For each business:
       Gate 1 (DNS)        — ALL tiers (if website exists)
       Email scraper       — scrapes website for real emails
       Footprint check     — drop leads with zero contact methods
       Gate 2 (SMTP)       — Starter/Growth/Pro tiers
       Gate 3 (DeepSeek)   — Pro tier only

UNIQUE FIELDS:
  - rating         (float, 1-5)
  - review_count   (int)
  - category       (string — e.g., "Cafe", "Restaurant")
  - address        (string)
  - opening_hours  (string — from OSM if available)
"""

import json
import asyncio
from typing import List, Dict, Any, Callable, Optional

from scraping.serper_search import (
    serper_search,
    serper_maps_search,
    build_smb_maps_queries,
)
from scraping.scrapingant import scrape_with_js
from scraping.email_scraper import enrich_lead_with_email
from scraping.osm_search import search_local_businesses
from ai.deepseek_middleware import execute_llm_payload, DEEPSEEK_SCOUT_MODEL
from validation.gate_dns import check_dns
from validation.gate_footprint import check_footprint
from validation.gate_smtp import check_smtp
from validation.gate_deepseek import check_deepseek
from dedup.hash_dedup import compute_domain_hash
from config import SCRAPINGANT_API_KEY


# ============================================================
# MAIN ENTRY POINT
# ============================================================
async def run_smb_maps(
    query: str,
    user_tier: str = "free",
    country: str = "",
    state_region: str = "",
    lead_target: int = 50,
    progress_callback: Optional[Callable] = None,
) -> List[Dict[str, Any]]:
    """Find local brick-and-mortar businesses matching the user's query."""
    leads: List[Dict[str, Any]] = []
    seen_hashes: set = set()

    # Build a location string for Serper (used by query builders + maps search)
    location_parts = [p for p in [state_region, country] if p]
    location = ", ".join(location_parts) if location_parts else ""

    print(f"[SMB_MAPS] Start — query='{query}', tier={user_tier}, target={lead_target}, loc='{location}'")

    # --------------------------------------------------------
    # PHASE 1: Serper maps + 10x Serper web searches (CONCURRENT)
    # --------------------------------------------------------
    if progress_callback:
        await progress_callback(15, f"Searching for local businesses in {location or 'your area'}...")

    web_queries = build_smb_maps_queries(query, location)

    # Run maps search + 10 web searches concurrently (11 total fetches)
    maps_task = serper_maps_search(query, location=location)
    web_tasks = [serper_search(q, num_results=10) for q in web_queries]

    all_fetches = await asyncio.gather(
        maps_task, *web_tasks, return_exceptions=True
    )

    maps_results = all_fetches[0]
    web_results_list = all_fetches[1:]

    # --- Process Serper maps results (structured — bypass DeepSeek) ---
    maps_leads: List[Dict[str, Any]] = []
    if isinstance(maps_results, list) and maps_results:
        print(f"[SMB_MAPS] Serper maps returned {len(maps_results)} places")
        for place in maps_results[:lead_target]:
            company_name = (place.get("title") or "").strip()
            if not company_name:
                continue
            website_url = place.get("website") or place.get("link") or "ABSENT"
            rating = _safe_float(place.get("rating"))
            review_count = _safe_int(place.get("ratingCount"))
            category = place.get("category") or "ABSENT"
            address = place.get("address") or "ABSENT"
            phone = place.get("phone") or "ABSENT"

            domain_hash = compute_domain_hash(website_url if website_url != "ABSENT" else company_name)
            if domain_hash in seen_hashes:
                continue
            seen_hashes.add(domain_hash)

            maps_leads.append({
                "domain_hash": domain_hash,
                "company_name": company_name,
                "website_url": website_url,
                "address": address,
                "phone": phone,
                "rating": rating,
                "review_count": review_count,
                "category": category,
                "opening_hours": "ABSENT",  # Serper maps doesn't return this
                "_source": "serper_maps",
            })
    elif isinstance(maps_results, Exception):
        print(f"[SMB_MAPS] Serper maps error: {maps_results}")
    else:
        print(f"[SMB_MAPS] Serper maps returned no results")

    # --- Process Serper web results (text — needs DeepSeek structuring) ---
    all_web_results: List[Dict[str, Any]] = []
    seen_urls: set = set()
    yelp_urls: List[str] = []
    for i, r in enumerate(web_results_list):
        if isinstance(r, Exception):
            print(f"[SMB_MAPS] Serper web search {i+1} error: {r}")
            continue
        if not isinstance(r, list):
            continue
        for item in r:
            url = item.get("link", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_web_results.append(item)
                if "yelp.com" in url.lower():
                    yelp_urls.append(url)

    print(f"[SMB_MAPS] Serper web: {len(all_web_results)} unique results across 10 queries "
          f"(Yelp URLs found: {len(yelp_urls)})")

    # --------------------------------------------------------
    # PHASE 1b: OPTIONAL — ScrapingAnt for Yelp pages (JS rendering)
    # --------------------------------------------------------
    scraped_texts: List[Dict[str, str]] = []
    if yelp_urls and SCRAPINGANT_API_KEY:
        if progress_callback:
            await progress_callback(25, f"Reading business directory pages...")

        # Cap to 3 Yelp pages to conserve ScrapingAnt credits
        yelp_fetch_tasks = [scrape_with_js(u) for u in yelp_urls[:3]]
        yelp_htmls = await asyncio.gather(*yelp_fetch_tasks, return_exceptions=True)

        for i, html in enumerate(yelp_htmls):
            if isinstance(html, str) and html:
                scraped_texts.append({
                    "source": f"Yelp (ScrapingAnt) — {yelp_urls[i]}",
                    "content": html[:8000],
                })
                print(f"[SMB_MAPS] Scraped Yelp via ScrapingAnt: {len(html)} chars")
            elif isinstance(html, Exception):
                print(f"[SMB_MAPS] Yelp ScrapingAnt error: {html}")

    # Format Serper web results as text for DeepSeek
    if all_web_results:
        serper_text = "\n\n".join(
            f"Title: {r.get('title', '')}\nURL: {r.get('link', '')}\nSnippet: {r.get('snippet', '')}"
            for r in all_web_results
        )
        scraped_texts.append({"source": "Google Search (Serper.dev)", "content": serper_text})

    # --------------------------------------------------------
    # PHASE 2: If still short of target, fall back to OSM
    # --------------------------------------------------------
    total_so_far = len(maps_leads)
    if total_so_far < lead_target:
        if progress_callback:
            await progress_callback(30, "Searching for more local businesses...")

        print(f"[SMB_MAPS] Only {total_so_far}/{lead_target} so far — trying OSM fallback")
        try:
            osm_location = location or country or state_region or query
            osm_businesses = await search_local_businesses(
                query, location=osm_location, radius=50000, limit=lead_target
            )
            if isinstance(osm_businesses, list) and osm_businesses:
                print(f"[SMB_MAPS] OSM returned {len(osm_businesses)} businesses")
                osm_text_blocks = []
                for biz in osm_businesses[:lead_target]:
                    name = biz.get("name", "").strip()
                    if not name:
                        continue
                    # Build a structured lead directly from OSM (bypass DeepSeek)
                    website_url = biz.get("website") or "ABSENT"
                    domain_hash = compute_domain_hash(website_url if website_url != "ABSENT" else name)
                    if domain_hash in seen_hashes:
                        continue
                    seen_hashes.add(domain_hash)

                    maps_leads.append({
                        "domain_hash": domain_hash,
                        "company_name": name,
                        "website_url": website_url,
                        "address": biz.get("address") or "ABSENT",
                        "phone": biz.get("phone") or "ABSENT",
                        "rating": 0.0,
                        "review_count": 0,
                        "category": biz.get("category") or "ABSENT",
                        "opening_hours": biz.get("opening_hours") or "ABSENT",
                        "_source": "openstreetmap",
                    })
                    # Also include OSM text in DeepSeek structuring in case
                    # OSM found businesses not yet captured
                    osm_text_blocks.append(
                        f"Name: {name}\nAddress: {biz.get('address', '')}\n"
                        f"Phone: {biz.get('phone', '')}\nWebsite: {biz.get('website', '')}\n"
                        f"Category: {biz.get('category', '')}"
                    )
                if osm_text_blocks:
                    scraped_texts.append({
                        "source": "OpenStreetMap (Overpass)",
                        "content": "\n\n".join(osm_text_blocks),
                    })
        except Exception as e:
            print(f"[SMB_MAPS] OSM fallback error: {e}")

    # --------------------------------------------------------
    # PHASE 3: DeepSeek structures any remaining text results
    # --------------------------------------------------------
    structured_businesses: List[Dict[str, Any]] = []
    if scraped_texts and len(leads) + len(maps_leads) < lead_target:
        if progress_callback:
            await progress_callback(40, "Finding businesses in your area...")

        combined_text = "\n\n".join(
            f"--- SOURCE: {s['source']} ---\n{s['content']}"
            for s in scraped_texts
        )

        structure_prompt = f"""
        You are a local business data extractor. Below is REAL TEXT scraped from the internet
        about local businesses related to: "{query}" in "{location or 'unspecified location'}".

        Extract EVERY REAL business mentioned in this text. Do NOT invent businesses.
        Be aggressive — aim for 20-50 businesses. Skip chain brands (Walmart, McDonald's, etc.).

        SCRAPED CONTENT:
        {combined_text[:14000]}

        For each REAL business, provide:
        - company_name: The exact business name as mentioned
        - website_url: Their website URL if mentioned (or "ABSENT")
        - address: Their physical street address if mentioned (or "ABSENT")
        - phone: Phone number if mentioned (or "ABSENT")
        - category: Business type if mentioned (or "ABSENT")
        - rating: Numeric rating if mentioned (or 0)
        - review_count: Integer review count if mentioned (or 0)

        Return a JSON object with a "businesses" array. Find up to {lead_target} businesses.
        If you cannot find data for a field, write "ABSENT" (or 0 for numeric).

        Example:
        {{
            "businesses": [
                {{
                    "company_name": "Mike's Roofing LLC",
                    "website_url": "https://mikesroofing.com",
                    "address": "123 Main St, Dallas, TX",
                    "phone": "(555) 123-4567",
                    "category": "Roofing Contractor",
                    "rating": 4.8,
                    "review_count": 127
                }}
            ]
        }}
        """

        try:
            response = await execute_llm_payload({
                "model": DEEPSEEK_SCOUT_MODEL,
                "messages": [
                    {"role": "system", "content": "You are a precise data extractor. Only extract REAL businesses mentioned in the provided text. Never invent data. Always respond with valid JSON. Use 'ABSENT' for missing string data and 0 for missing numeric data."},
                    {"role": "user", "content": structure_prompt},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.1,
            })

            content = response.get("choices", [{}])[0].get("message", {}).get("content", "{}")
            parsed = json.loads(content)
            structured_businesses = parsed.get("businesses", parsed.get("results", []))
            if isinstance(parsed, list):
                structured_businesses = parsed

            print(f"[SMB_MAPS] DeepSeek extracted {len(structured_businesses)} candidate businesses")
        except Exception as e:
            print(f"[SMB_MAPS] DeepSeek structuring error: {e}")

    # --------------------------------------------------------
    # PHASE 4: Validate & enrich maps_leads (already structured)
    # --------------------------------------------------------
    if progress_callback:
        await progress_callback(50, f"Verifying businesses...")

    # Process maps leads first (they have the richest structured data)
    for biz in maps_leads:
        if len(leads) >= lead_target:
            break

        company_name = biz["company_name"]
        website_url = biz.get("website_url", "ABSENT")

        # Gate 1: DNS Check (ALL tiers — only if website exists)
        gates_passed = 0
        if website_url != "ABSENT":
            try:
                domain_ok, _ = await check_dns(website_url)
                if not domain_ok:
                    print(f"[SMB_MAPS] DNS failed for {website_url} — DROPPED")
                    continue
                gates_passed = 1
            except Exception as e:
                print(f"[SMB_MAPS] DNS check error for {website_url}: {e} — continuing anyway")
                gates_passed = 1

        # Email scraper enrichment (scrapes website for real emails)
        try:
            enrichment = await enrich_lead_with_email(company_name, website_url)
        except Exception as e:
            print(f"[SMB_MAPS] Email scraper error for {company_name}: {e}")
            enrichment = {
                "verified_email": "ABSENT", "phone": "ABSENT",
                "facebook": "ABSENT", "instagram": "ABSENT", "linkedin": "ABSENT",
            }

        # Prefer scraped phone over maps phone if maps had none
        phone = biz.get("phone", "ABSENT")
        if phone == "ABSENT" or not phone:
            phone = enrichment.get("phone", "ABSENT")

        lead = {
            "domain_hash": biz["domain_hash"],
            "company_name": company_name,
            "website_url": website_url,
            "dm_name": "ABSENT",
            "dm_position": "ABSENT",
            "verified_email": enrichment.get("verified_email", "ABSENT"),
            "is_catchall": False,
            "linkedin": enrichment.get("linkedin", "ABSENT"),
            "instagram": enrichment.get("instagram", "ABSENT"),
            "facebook": enrichment.get("facebook", "ABSENT"),
            "phone": phone,
            "address": biz.get("address", "ABSENT"),
            "rating": biz.get("rating", 0.0),
            "review_count": biz.get("review_count", 0),
            "category": biz.get("category", "ABSENT"),
            "opening_hours": biz.get("opening_hours", "ABSENT"),
            "validation_gates_passed": gates_passed,
        }

        # Pre-filter: Footprint check
        if not check_footprint(lead):
            print(f"[SMB_MAPS] Footprint failed for {company_name} — DROPPED")
            continue

        # Gate 2: SMTP (Starter/Growth/Pro)
        if user_tier in ("starter", "growth", "pro") and lead["verified_email"] != "ABSENT":
            try:
                smtp_ok, is_catchall = await check_smtp(lead["verified_email"])
                lead["is_catchall"] = is_catchall
                if not smtp_ok and not is_catchall:
                    print(f"[SMB_MAPS] SMTP failed for {lead['verified_email']} — DROPPED")
                    continue
                gates_passed = 2
                lead["validation_gates_passed"] = gates_passed
            except Exception as e:
                print(f"[SMB_MAPS] SMTP error for {lead['verified_email']}: {e} — lenient accept")
                gates_passed = 2
                lead["validation_gates_passed"] = gates_passed

        # Gate 3: DeepSeek AI (Pro only)
        if user_tier == "pro" and lead["verified_email"] != "ABSENT":
            try:
                deepseek_ok, is_role, _ = await check_deepseek(lead["verified_email"], company_name)
                if not deepseek_ok:
                    print(f"[SMB_MAPS] DeepSeek Gate 3 rejected {lead['verified_email']} — DROPPED")
                    continue
                if is_role:
                    lead["is_catchall"] = True
                gates_passed = 3
                lead["validation_gates_passed"] = gates_passed
            except Exception as e:
                print(f"[SMB_MAPS] DeepSeek Gate 3 error for {lead['verified_email']}: {e} — lenient accept")
                gates_passed = 3
                lead["validation_gates_passed"] = gates_passed

        # Strip internal-only _source key before returning
        lead.pop("_source", None)
        leads.append(lead)

    # --------------------------------------------------------
    # PHASE 5: Validate & enrich DeepSeek-structured businesses
    # --------------------------------------------------------
    if structured_businesses and len(leads) < lead_target:
        if progress_callback:
            await progress_callback(70, f"Verifying more businesses...")

        for biz in structured_businesses:
            if len(leads) >= lead_target:
                break

            company_name = (biz.get("company_name") or "").strip()
            website_url = biz.get("website_url", "ABSENT")
            if not company_name or company_name == "ABSENT":
                continue

            domain_hash = compute_domain_hash(website_url if website_url != "ABSENT" else company_name)
            if domain_hash in seen_hashes:
                continue
            seen_hashes.add(domain_hash)

            # Gate 1: DNS Check
            gates_passed = 0
            if website_url != "ABSENT":
                try:
                    domain_ok, _ = await check_dns(website_url)
                    if not domain_ok:
                        print(f"[SMB_MAPS] DNS failed for {website_url} — DROPPED")
                        continue
                    gates_passed = 1
                except Exception as e:
                    print(f"[SMB_MAPS] DNS check error for {website_url}: {e} — continuing anyway")
                    gates_passed = 1

            # Email scraper enrichment
            try:
                enrichment = await enrich_lead_with_email(company_name, website_url)
            except Exception as e:
                print(f"[SMB_MAPS] Email scraper error for {company_name}: {e}")
                enrichment = {
                    "verified_email": "ABSENT", "phone": "ABSENT",
                    "facebook": "ABSENT", "instagram": "ABSENT", "linkedin": "ABSENT",
                }

            # Prefer scraped phone over DeepSeek phone
            ds_phone = biz.get("phone", "ABSENT")
            phone = enrichment.get("phone", "ABSENT")
            if phone == "ABSENT" or not phone:
                phone = ds_phone

            lead = {
                "domain_hash": domain_hash,
                "company_name": company_name,
                "website_url": website_url,
                "dm_name": "ABSENT",
                "dm_position": "ABSENT",
                "verified_email": enrichment.get("verified_email", "ABSENT"),
                "is_catchall": False,
                "linkedin": enrichment.get("linkedin", "ABSENT"),
                "instagram": enrichment.get("instagram", "ABSENT"),
                "facebook": enrichment.get("facebook", "ABSENT"),
                "phone": phone,
                "address": biz.get("address", "ABSENT"),
                "rating": _safe_float(biz.get("rating")),
                "review_count": _safe_int(biz.get("review_count")),
                "category": biz.get("category", "ABSENT"),
                "opening_hours": "ABSENT",
                "validation_gates_passed": gates_passed,
            }

            if not check_footprint(lead):
                print(f"[SMB_MAPS] Footprint failed for {company_name} — DROPPED")
                continue

            # Gate 2: SMTP (Starter/Growth/Pro)
            if user_tier in ("starter", "growth", "pro") and lead["verified_email"] != "ABSENT":
                try:
                    smtp_ok, is_catchall = await check_smtp(lead["verified_email"])
                    lead["is_catchall"] = is_catchall
                    if not smtp_ok and not is_catchall:
                        print(f"[SMB_MAPS] SMTP failed for {lead['verified_email']} — DROPPED")
                        continue
                    gates_passed = 2
                    lead["validation_gates_passed"] = gates_passed
                except Exception as e:
                    print(f"[SMB_MAPS] SMTP error for {lead['verified_email']}: {e} — lenient accept")
                    gates_passed = 2
                    lead["validation_gates_passed"] = gates_passed

            # Gate 3: DeepSeek AI (Pro only)
            if user_tier == "pro" and lead["verified_email"] != "ABSENT":
                try:
                    deepseek_ok, is_role, _ = await check_deepseek(lead["verified_email"], company_name)
                    if not deepseek_ok:
                        print(f"[SMB_MAPS] DeepSeek Gate 3 rejected {lead['verified_email']} — DROPPED")
                        continue
                    if is_role:
                        lead["is_catchall"] = True
                    gates_passed = 3
                    lead["validation_gates_passed"] = gates_passed
                except Exception as e:
                    print(f"[SMB_MAPS] DeepSeek Gate 3 error for {lead['verified_email']}: {e} — lenient accept")
                    gates_passed = 3
                    lead["validation_gates_passed"] = gates_passed

            leads.append(lead)

    if progress_callback:
        await progress_callback(90, f"Found {len(leads)} verified local businesses")

    print(f"[SMB_MAPS] Returning {len(leads)} verified leads")
    return leads


# ============================================================
# HELPERS
# ============================================================
def _safe_float(value: Any) -> float:
    """Convert a value to a float, returning 0.0 on failure."""
    try:
        if value is None or value == "" or value == "ABSENT":
            return 0.0
        return float(value)
    except (ValueError, TypeError):
        return 0.0


def _safe_int(value: Any) -> int:
    """Convert a value to an int, returning 0 on failure."""
    try:
        if value is None or value == "" or value == "ABSENT":
            return 0
        return int(float(value))
    except (ValueError, TypeError):
        return 0
