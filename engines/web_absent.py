"""
BAD DECISION — Engine 3: web_absent (Businesses Without Websites)
================================================================
This engine finds businesses that exist ONLY on aggregator sites
(Yelp, Houzz, Etsy, Facebook, etc.) and do NOT have their own
standalone website. These are prime targets for web-design agencies.

PIPELINE:
  1. 10x concurrent Serper web searches (build_web_absent_queries —
     targets site:yelp.com, site:houzz.com, site:etsy.com, etc.)
  2. ScrapingAnt fetches any Yelp/Houzz URLs found (JS rendering —
     currently 403 without it)
  3. DeepSeek structures the text, identifying businesses WITHOUT
     external websites
  4. Email scraper tries aggregator profile pages
  5. SKIP Gate 1 (DNS) — these businesses have no website to check
  6. Footprint check → Gate 2 (SMTP, Starter+) → Gate 3 (DeepSeek, Pro)
  7. HARD FILTER: Drop any business that HAS an external website

UNIQUE FIELDS:
  - aggregator_source  (string — "Yelp", "Houzz", "Etsy", "Facebook", etc.)
  - aggregator_url     (string — direct link to profile)
  - aggregator_rating  (float — rating on the aggregator)
  - needs_website      (boolean — always true for this engine)
"""

import json
import asyncio
from urllib.parse import urlparse
from typing import List, Dict, Any, Callable, Optional

from scraping.serper_search import serper_search, build_web_absent_queries
from scraping.browserless import scrape_with_js
from scraping.stealth_fetcher import (
    extract_links_from_html,
    build_yelp_search_url,
    build_houzz_search_url,
)
from scraping.email_scraper import enrich_lead_with_email
from ai.deepseek_middleware import execute_llm_payload, DEEPSEEK_SCOUT_MODEL
from validation.gate_footprint import check_footprint
from validation.gate_smtp import check_smtp
from validation.gate_deepseek import check_deepseek
from dedup.hash_dedup import compute_domain_hash
from config import BROWSERLESS_API_KEY


# Aggregator domains we expect businesses to be ON (not their own website)
AGGREGATOR_DOMAINS = {
    "yelp.com", "houzz.com", "etsy.com", "facebook.com",
    "instagram.com", "nextdoor.com", "angi.com", "thumbtack.com",
    "bark.com", "google.com", "maps.google.com",
}


# ============================================================
# MAIN ENTRY POINT
# ============================================================
async def run_web_absent(
    query: str,
    user_tier: str = "free",
    country: str = "",
    state_region: str = "",
    lead_target: int = 50,
    progress_callback: Optional[Callable] = None,
) -> List[Dict[str, Any]]:
    """Find businesses without their own website — only on aggregator sites."""
    leads: List[Dict[str, Any]] = []
    seen_hashes: set = set()

    location_parts = [p for p in [state_region, country] if p]
    location = ", ".join(location_parts) if location_parts else ""

    print(f"[WEB_ABSENT] Start — query='{query}', tier={user_tier}, target={lead_target}, loc='{location}'")

    # --------------------------------------------------------
    # PHASE 1: 10x Serper web searches + ScrapingAnt for Yelp/Houzz (CONCURRENT)
    # --------------------------------------------------------
    if progress_callback:
        await progress_callback(15, "Searching directories for businesses that need a website...")

    web_queries = build_web_absent_queries(query, location)
    web_tasks = [serper_search(q, num_results=10) for q in web_queries]

    # Optionally fetch Yelp + Houzz search pages via ScrapingAnt (JS rendering)
    js_tasks: List[Any] = []
    js_source_names: List[str] = []
    if BROWSERLESS_API_KEY:
        yelp_url = build_yelp_search_url(query, state_region or country)
        houzz_url = build_houzz_search_url(query)
        js_tasks = [scrape_with_js(yelp_url), scrape_with_js(houzz_url)]
        js_source_names = ["Yelp (ScrapingAnt)", "Houzz (ScrapingAnt)"]
    else:
        print("[WEB_ABSENT] ScrapingAnt not configured — skipping Yelp/Houzz JS fetches")

    all_fetches = await asyncio.gather(*web_tasks, *js_tasks, return_exceptions=True)

    web_results_list = all_fetches[:len(web_tasks)]
    js_results_list = all_fetches[len(web_tasks):]

    # --- Process Serper web results (dedup by URL) ---
    all_web_results: List[Dict[str, Any]] = []
    seen_urls: set = set()
    for i, r in enumerate(web_results_list):
        if isinstance(r, Exception):
            print(f"[WEB_ABSENT] Serper web search {i+1} error: {r}")
            continue
        if not isinstance(r, list):
            continue
        for item in r:
            url = item.get("link", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_web_results.append(item)

    print(f"[WEB_ABSENT] Serper web: {len(all_web_results)} unique results across 10 queries")

    # Collect aggregator profile URLs (from Serper results) for later ScrapingAnt deep-fetch
    aggregator_profile_urls: List[str] = []
    for item in all_web_results:
        url = item.get("link", "")
        if not url:
            continue
        try:
            host = (urlparse(url).netloc or "").lower()
            if any(agg in host for agg in AGGREGATOR_DOMAINS):
                aggregator_profile_urls.append(url)
        except Exception:
            pass

    # --- Process ScrapingAnt JS-rendered pages ---
    scraped_texts: List[Dict[str, Any]] = []
    for i, html in enumerate(js_results_list):
        source_name = js_source_names[i] if i < len(js_source_names) else f"JS source {i+1}"
        if isinstance(html, str) and html:
            # Extract external (non-aggregator) links for the hard filter
            base_url = "https://www.yelp.com" if "yelp" in source_name.lower() else "https://www.houzz.com"
            links = extract_links_from_html(html, base_url=base_url)
            scraped_texts.append({
                "source": source_name,
                "content": html[:8000],
                "links": links,
            })
            print(f"[WEB_ABSENT] Scraped {source_name}: {len(html)} chars, {len(links)} links")
        elif isinstance(html, Exception):
            print(f"[WEB_ABSENT] {source_name} fetch error: {html}")

    # Format Serper web results as text for DeepSeek
    if all_web_results:
        serper_text = "\n\n".join(
            f"Title: {r.get('title', '')}\nURL: {r.get('link', '')}\nSnippet: {r.get('snippet', '')}"
            for r in all_web_results
        )
        scraped_texts.append({
            "source": "Google Search (Serper.dev)",
            "content": serper_text,
            "links": [r.get("link", "") for r in all_web_results if r.get("link")],
        })

    if not scraped_texts:
        print(f"[WEB_ABSENT] All sources failed — no data to process")
        return []

    combined_text = "\n\n".join(
        f"--- SOURCE: {s['source']} ---\n{s['content']}"
        for s in scraped_texts
    )

    # Collect all external (non-aggregator) links — these signal HAS a website
    all_external_links: set = set()
    for s in scraped_texts:
        for link in s.get("links", []) or []:
            try:
                host = (urlparse(link).netloc or "").lower()
                if host and not any(agg in host for agg in AGGREGATOR_DOMAINS):
                    all_external_links.add(link)
            except Exception:
                pass

    # --------------------------------------------------------
    # PHASE 1b: OPTIONAL — ScrapingAnt deep-fetch on aggregator profile URLs
    # --------------------------------------------------------
    if BROWSERLESS_API_KEY and aggregator_profile_urls and len(leads) < lead_target:
        # Cap at 3 profile fetches to conserve ScrapingAnt credits
        deep_fetch_urls = aggregator_profile_urls[:3]
        if progress_callback:
            await progress_callback(25, f"Reading business profiles...")

        print(f"[WEB_ABSENT] ScrapingAnt deep-fetch on {len(deep_fetch_urls)} profile URLs")
        deep_tasks = [scrape_with_js(u) for u in deep_fetch_urls]
        deep_htmls = await asyncio.gather(*deep_tasks, return_exceptions=True)

        for i, html in enumerate(deep_htmls):
            if isinstance(html, str) and html:
                scraped_texts.append({
                    "source": f"Aggregator profile (ScrapingAnt) — {deep_fetch_urls[i]}",
                    "content": html[:6000],
                    "links": extract_links_from_html(html, base_url=deep_fetch_urls[i]),
                })
                # Update external links set
                for link in extract_links_from_html(html, base_url=deep_fetch_urls[i]):
                    try:
                        host = (urlparse(link).netloc or "").lower()
                        if host and not any(agg in host for agg in AGGREGATOR_DOMAINS):
                            all_external_links.add(link)
                    except Exception:
                        pass
            elif isinstance(html, Exception):
                print(f"[WEB_ABSENT] ScrapingAnt profile fetch error: {html}")

        # Rebuild combined_text with the new sources
        combined_text = "\n\n".join(
            f"--- SOURCE: {s['source']} ---\n{s['content']}"
            for s in scraped_texts
        )

    # --------------------------------------------------------
    # PHASE 2: DeepSeek — Structure the scraped data
    # --------------------------------------------------------
    if progress_callback:
        await progress_callback(35, "Finding businesses that need a website...")

    print(f"[WEB_ABSENT] DeepSeek structuring phase")

    structure_prompt = f"""
    You are a business data extractor. Below is REAL TEXT scraped from the internet
    about businesses listed on aggregator platforms related to: "{query}"
    in "{location or 'unspecified location'}".

    Extract REAL businesses mentioned in this text. Do NOT invent businesses.

    HARD RULES:
    - The business must NOT have its own standalone external website
    - The business should exist ONLY on the aggregator platform (Yelp, Houzz, Etsy, etc.)
    - If a business clearly has its own external http(s) domain, EXCLUDE it

    SCRAPED CONTENT:
    {combined_text[:12000]}

    For each REAL business you find, provide:
    - company_name: The exact business name as mentioned
    - aggregator_source: Which platform they are on (Yelp, Houzz, Etsy, Facebook, etc.)
    - aggregator_url: Direct URL to their profile on the aggregator (or "ABSENT")
    - aggregator_rating: Numeric rating if mentioned (or 0)
    - has_external_website: true or false (MUST be false to be included)
    - phone: Phone number if mentioned (or "ABSENT")
    - email: Email address if mentioned on the aggregator profile (or "ABSENT")

    Return a JSON object with a "businesses" array. Find up to {lead_target} businesses.
    If you cannot find data for a field, write "ABSENT" (or 0 for rating).

    Example:
    {{
        "businesses": [
            {{
                "company_name": "Sunset Bakery",
                "aggregator_source": "Yelp",
                "aggregator_url": "https://yelp.com/biz/sunset-bakery",
                "aggregator_rating": 4.5,
                "has_external_website": false,
                "phone": "(555) 987-6543",
                "email": "ABSENT"
            }}
        ]
    }}
    """

    businesses: List[Dict[str, Any]] = []
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
        await progress_callback(50, f"Verifying {min(len(businesses), lead_target)} businesses...")

    for biz in businesses[:lead_target]:
        if len(leads) >= lead_target:
            break

        company_name = (biz.get("company_name") or "").strip()
        aggregator_source = (biz.get("aggregator_source") or "ABSENT").strip() or "ABSENT"
        aggregator_url = biz.get("aggregator_url", "ABSENT")
        aggregator_rating = _safe_float(biz.get("aggregator_rating"))
        has_external_website = biz.get("has_external_website", False)
        ds_phone = biz.get("phone", "ABSENT")
        ds_email = biz.get("email", "ABSENT")

        if not company_name or company_name == "ABSENT":
            continue

        # HARD FILTER: Block aggregator platform names from being treated as businesses
        # (DeepSeek sometimes extracts "Yelp", "Houzz", "Facebook" etc. as businesses)
        PLATFORM_NAMES = {
            "yelp", "houzz", "etsy", "facebook", "instagram", "google",
            "google maps", "thumbtack", "angi", "bark", "nextdoor",
            "linkedin", "twitter", "reddit", "youtube", "tiktok",
            "tripadvisor", "yellow pages", "opencorporates",
            "yelp.com", "houzz.com", "etsy.com", "facebook.com",
            "instagram.com", "google.com", "thumbtack.com", "angi.com",
            "bark.com", "nextdoor.com", "linkedin.com", "twitter.com",
            "reddit.com", "youtube.com", "tiktok.com", "tripadvisor.com",
        }
        if company_name.lower().strip() in PLATFORM_NAMES:
            print(f"[WEB_ABSENT] '{company_name}' is a platform, not a business — DROPPED")
            continue

        # HARD FILTER: Block aggregator URLs that are just the platform homepage
        # (e.g., yelp.com, houzz.com — not yelp.com/biz/specific-business)
        if aggregator_url != "ABSENT" and aggregator_url != "":
            from urllib.parse import urlparse
            try:
                parsed_url = urlparse(aggregator_url if aggregator_url.startswith("http") else f"https://{aggregator_url}")
                domain = (parsed_url.hostname or "").lower().replace("www.", "")
                path = (parsed_url.path or "").strip("/")
                # If the URL is just the domain with no path, it's a homepage, not a business profile
                if not path and domain in {"yelp.com", "houzz.com", "etsy.com", "facebook.com",
                                           "instagram.com", "google.com", "thumbtack.com", "angi.com",
                                           "bark.com", "nextdoor.com", "linkedin.com", "tripadvisor.com"}:
                    print(f"[WEB_ABSENT] '{company_name}' URL is platform homepage — DROPPED")
                    continue
            except:
                pass

        # HARD FILTER: Must NOT have an external website
        if has_external_website is True or str(has_external_website).lower() == "true":
            print(f"[WEB_ABSENT] {company_name} has external website — DROPPED")
            continue

        # Cross-check: if the aggregator_url itself links to an external site, drop
        if aggregator_url != "ABSENT" and aggregator_url in all_external_links:
            print(f"[WEB_ABSENT] {company_name} aggregator URL is external — DROPPED")
            continue

        # Detect if the company_name itself includes a standalone domain
        # (DeepSeek sometimes pastes the website into company_name)
        if _looks_like_external_domain(company_name, all_external_links):
            print(f"[WEB_ABSENT] {company_name} looks like an external domain — DROPPED")
            continue

        domain_hash = compute_domain_hash(aggregator_url if aggregator_url != "ABSENT" else company_name)
        if domain_hash in seen_hashes:
            continue
        seen_hashes.add(domain_hash)

        # SKIP Gate 1 (DNS) — these businesses have no website to check
        gates_passed = 0

        # Email scraper tries the aggregator profile page (no website)
        # We pass aggregator_url as the "website" so the scraper tries to extract
        # emails/phones from the aggregator profile itself.
        enrichment_url = aggregator_url if aggregator_url != "ABSENT" else "ABSENT"
        try:
            enrichment = await enrich_lead_with_email(company_name, enrichment_url)
        except Exception as e:
            print(f"[WEB_ABSENT] Email scraper error for {company_name}: {e}")
            enrichment = {
                "verified_email": "ABSENT", "phone": "ABSENT",
                "facebook": "ABSENT", "instagram": "ABSENT", "linkedin": "ABSENT",
            }

        # Prefer DeepSeek's email/phone if scraper didn't find any
        verified_email = enrichment.get("verified_email", "ABSENT")
        if (verified_email == "ABSENT" or not verified_email) and ds_email != "ABSENT":
            verified_email = ds_email

        phone = enrichment.get("phone", "ABSENT")
        if (phone == "ABSENT" or not phone) and ds_phone != "ABSENT":
            phone = ds_phone

        lead = {
            "domain_hash": domain_hash,
            "company_name": company_name,
            "website_url": "ABSENT",  # By definition these businesses have no website
            "dm_name": "ABSENT",
            "dm_position": "ABSENT",
            "verified_email": verified_email,
            "is_catchall": False,
            "linkedin": "ABSENT",
            "instagram": "ABSENT",
            "facebook": "ABSENT",
            "phone": phone,
            "aggregator_source": aggregator_source,
            "aggregator_url": aggregator_url,
            "aggregator_rating": aggregator_rating,
            "needs_website": True,  # Always true for this engine
            "validation_gates_passed": gates_passed,
        }

        # Pre-filter: Footprint check
        if not check_footprint(lead):
            print(f"[WEB_ABSENT] Footprint failed for {company_name} — DROPPED")
            continue

        # Gate 2: SMTP (Starter/Growth/Pro)
        if user_tier in ("starter", "growth", "pro") and lead["verified_email"] != "ABSENT":
            try:
                smtp_ok, is_catchall = await check_smtp(lead["verified_email"])
                lead["is_catchall"] = is_catchall
                if not smtp_ok and not is_catchall:
                    print(f"[WEB_ABSENT] SMTP failed for {lead['verified_email']} — DROPPED")
                    continue
                gates_passed = 2
                lead["validation_gates_passed"] = gates_passed
            except Exception as e:
                print(f"[WEB_ABSENT] SMTP error for {lead['verified_email']}: {e} — lenient accept")
                gates_passed = 2
                lead["validation_gates_passed"] = gates_passed

        # Gate 3: DeepSeek AI (Pro only)
        if user_tier == "pro" and lead["verified_email"] != "ABSENT":
            try:
                deepseek_ok, is_role, _ = await check_deepseek(lead["verified_email"], company_name)
                if not deepseek_ok:
                    print(f"[WEB_ABSENT] DeepSeek Gate 3 rejected {lead['verified_email']} — DROPPED")
                    continue
                if is_role:
                    lead["is_catchall"] = True
                gates_passed = 3
                lead["validation_gates_passed"] = gates_passed
            except Exception as e:
                print(f"[WEB_ABSENT] DeepSeek Gate 3 error for {lead['verified_email']}: {e} — lenient accept")
                gates_passed = 3
                lead["validation_gates_passed"] = gates_passed

        leads.append(lead)

    if progress_callback:
        await progress_callback(90, f"Found {len(leads)} verified businesses without websites")

    print(f"[WEB_ABSENT] Returning {len(leads)} verified leads")
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


def _looks_like_external_domain(name: str, external_links: set) -> bool:
    """Detect if a company_name string is actually a standalone external domain."""
    if not name:
        return False
    name_lower = name.lower().strip()
    # Patterns like "abcroofing.com" without aggregator suffix
    if " " not in name_lower and "." in name_lower and not any(
        agg in name_lower for agg in AGGREGATOR_DOMAINS
    ):
        # Looks like a domain — does it match any external link we found?
        for link in external_links:
            try:
                host = (urlparse(link).netloc or "").lower()
                if host and host in name_lower:
                    return True
            except Exception:
                pass
    return False
