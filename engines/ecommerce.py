"""
BAD DECISION — Engine 3: Ecommerce Brands
==========================================
Finds online stores (Shopify, WooCommerce, BigCommerce, etc.) and returns
deep ecommerce data: product count, categories, pricing, tech stack, etc.
Capable of returning thousands of leads per search.

PIPELINE:
  1. Discovery: Smart Google search queries for ecommerce stores
  2. Platform detection: Check what ecommerce platform each store uses
  3. Deep enrichment: Fetch Shopify product catalog (free public API)
  4. Tech stack detection: Detect email marketing, ad tracking, etc.
  5. Contact enrichment: Scrape homepage for emails, check messaging apps

UNIQUE FIELDS:
  - ecommerce_platform (Shopify / WooCommerce / BigCommerce / etc.)
  - product_count, product_categories, average_price, price_range
  - store_currency, estimated_revenue
  - tech_stack[], uses_email_marketing, uses_ad_tracking, uses_subscriptions
  - store_age_days, social_media_links[]
  - is_whatsapp, is_telegram
"""

import json
import asyncio
from typing import List, Dict, Any, Callable, Optional

from scraping.serper_search import serper_search
from scraping.browserless import scrape_with_js
from scraping.email_scraper import enrich_lead_with_email
from scraping.ecommerce_detector import detect_ecommerce_platform
from scraping.shopify_products import fetch_shopify_products
from scraping.tech_stack_detector import detect_tech_stack
from scraping.checknumber import check_messaging_platforms
from scraping.location_mapper import build_location_string
from scraping.phone_normalizer import normalize_phone
from scraping.url_cleaner import extract_root_website
from scraping.domain_age import get_domain_age_days
from ai.deepseek_middleware import execute_llm_payload, DEEPSEEK_SCOUT_MODEL
from dedup.hash_dedup import compute_domain_hash
from validation.gate_footprint import check_footprint


# ============================================================
# MAIN ENTRY POINT
# ============================================================
async def run_ecommerce(
    query: str,
    user_tier: str = "free",
    country: str = "",
    state_region: str = "",
    lead_target: int = 500,
    progress_callback: Optional[Callable] = None,
) -> List[Dict[str, Any]]:
    """Find ecommerce brands matching the user's query."""

    leads: List[Dict[str, Any]] = []
    seen_hashes: set = set()

    # Build location string from ISO codes (NG + LA → "Lagos, Nigeria")
    location = build_location_string(country, state_region)

    print(f"[ECOMMERCE] Start: query='{query}', tier={user_tier}, target={lead_target}, loc='{location}'")

    # --------------------------------------------------------
    # PHASE 1: Discovery — find ecommerce store URLs
    # --------------------------------------------------------
    if progress_callback:
        await progress_callback(15, "Searching for online stores...")

    web_queries = _build_ecommerce_queries(query, location)
    web_tasks = [serper_search(q, num_results=10, country_code=country) for q in web_queries]
    all_fetches = await asyncio.gather(*web_tasks, return_exceptions=True)

    all_urls: List[str] = []
    seen_urls: set = set()
    for r in all_fetches:
        if isinstance(r, Exception) or not isinstance(r, list):
            continue
        for item in r:
            url = item.get("link", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_urls.append(url)

    print(f"[ECOMMERCE] Discovered {len(all_urls)} unique URLs from {len(web_queries)} queries")

    # --------------------------------------------------------
    # PHASE 2: Platform detection + deep enrichment
    # --------------------------------------------------------
    if progress_callback:
        await progress_callback(30, "Identifying online stores...")

    # Process URLs in batches of 20 (to avoid overwhelming the server)
    batch_size = 20
    for i in range(0, len(all_urls), batch_size):
        if len(leads) >= lead_target:
            break

        batch = all_urls[i:i + batch_size]
        batch_tasks = [_process_ecommerce_url(url, query, seen_hashes) for url in batch]
        batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)

        for result in batch_results:
            if isinstance(result, Exception) or not result:
                continue
            if len(leads) >= lead_target:
                break
            leads.append(result)

        if progress_callback:
            progress = 30 + int(60 * len(leads) / lead_target)
            await progress_callback(min(progress, 90), f"Found {len(leads)} online stores...")

    # --------------------------------------------------------
    # PHASE 3: Messaging platform check (WhatsApp + Telegram)
    # Normalize phones to E.164 first, then check concurrently.
    # --------------------------------------------------------
    if progress_callback:
        await progress_callback(90, "Checking messaging platforms...")

    if user_tier in ("growth", "pro"):
        # Normalize all phones first
        for lead in leads:
            p = lead.get("phone", "ABSENT")
            if p and p != "ABSENT":
                lead["phone"] = normalize_phone(p, country)

        # Check messaging platforms concurrently (5 at a time)
        MAX_CONCURRENT = 5
        sem = asyncio.Semaphore(MAX_CONCURRENT)

        async def check_one(lead):
            async with sem:
                p = lead.get("phone", "ABSENT")
                if p and p != "ABSENT":
                    try:
                        messaging = await check_messaging_platforms(p)
                        lead["is_whatsapp"] = messaging["whatsapp"]
                        lead["is_telegram"] = messaging["telegram"]
                        lead["messaging_checked"] = True
                    except Exception as e:
                        print(f"[ECOMMERCE] CheckNumber error for {p}: {e}")
                        lead["is_whatsapp"] = False
                        lead["is_telegram"] = False
                        lead["messaging_checked"] = False

        await asyncio.gather(*[check_one(l) for l in leads], return_exceptions=True)

    if progress_callback:
        await progress_callback(95, f"Found {len(leads)} verified ecommerce brands")

    print(f"[ECOMMERCE] Returning {len(leads)} leads")
    return leads


async def _process_ecommerce_url(
    url: str,
    query: str,
    seen_hashes: set,
) -> Optional[Dict[str, Any]]:
    """Process a single URL: detect platform, enrich, build lead."""

    # Skip non-http URLs
    if not url.startswith("http"):
        return None

    # Skip aggregators and social media
    skip_domains = ["facebook.com", "instagram.com", "twitter.com", "youtube.com",
                    "pinterest.com", "linkedin.com", "reddit.com", "yelp.com",
                    "wikipedia.org", "amazon.com", "etsy.com", "ebay.com",
                    "google.com", "tiktok.com", "app.shopify.com"]
    for skip in skip_domains:
        if skip in url.lower():
            return None

    # Dedup
    domain_hash = compute_domain_hash(url)
    if domain_hash in seen_hashes:
        return None
    seen_hashes.add(domain_hash)

    # Detect ecommerce platform
    platform = await detect_ecommerce_platform(url)
    if not platform:
        return None  # Not an ecommerce store, skip

    # Extract company name from URL
    from urllib.parse import urlparse
    parsed = urlparse(url)
    domain = parsed.hostname or ""
    if domain.startswith("www."):
        domain = domain[4:]
    company_name = domain.split(".")[0].replace("-", " ").replace("_", " ").title()

    # Build initial lead
    lead = {
        "domain_hash": domain_hash,
        "company_name": company_name,
        "website_url": url,
        "dm_name": "ABSENT",
        "dm_position": "ABSENT",
        "verified_email": "ABSENT",
        "is_catchall": False,
        "linkedin": "ABSENT",
        "instagram": "ABSENT",
        "facebook": "ABSENT",
        "phone": "ABSENT",
        "ecommerce_platform": platform,
        "validation_gates_passed": 0,
    }

    # Deep enrichment for Shopify stores (free public API)
    if platform == "Shopify":
        product_data = await fetch_shopify_products(url)
        if product_data:
            lead["product_count"] = product_data["product_count"]
            lead["product_categories"] = product_data["product_categories"]
            lead["average_price"] = product_data["average_price"]
            lead["price_range"] = product_data["price_range"]
            lead["store_currency"] = product_data["store_currency"]

    # Tech stack detection (free)
    tech_data = await detect_tech_stack(url)
    lead["tech_stack"] = tech_data["tools"]
    lead["uses_email_marketing"] = tech_data["uses_email_marketing"]
    lead["uses_ad_tracking"] = tech_data["uses_ad_tracking"]
    lead["uses_subscriptions"] = tech_data["uses_subscriptions"]
    if tech_data["social_media"]:
        lead["social_media_links"] = tech_data["social_media"]

    # Domain age lookup (free RDAP query)
    age_days = await get_domain_age_days(url)
    if age_days and age_days > 0:
        lead["store_age_days"] = age_days

    # Estimate revenue based on product count + platform
    lead["estimated_revenue"] = _estimate_revenue(lead)

    # Contact enrichment (scrape homepage for emails)
    try:
        enrichment = await enrich_lead_with_email(company_name, url)
        lead["verified_email"] = enrichment.get("verified_email", "ABSENT")
        lead["phone"] = enrichment.get("phone", "ABSENT")
        lead["facebook"] = enrichment.get("facebook", "ABSENT")
        lead["instagram"] = enrichment.get("instagram", "ABSENT")
        lead["linkedin"] = enrichment.get("linkedin", "ABSENT")
    except Exception as e:
        print(f"[ECOMMERCE] Email scraper error for {company_name}: {e}")

    # Footprint check (must have at least one contact method)
    if not check_footprint(lead):
        return None

    lead["validation_gates_passed"] = 1
    return lead


def _estimate_revenue(lead: Dict[str, Any]) -> str:
    """Estimate revenue based on product count, price, and platform."""
    product_count = lead.get("product_count", 0)
    avg_price_str = lead.get("average_price", "ABSENT")

    try:
        avg_price = float(avg_price_str) if avg_price_str != "ABSENT" else 50
    except (ValueError, TypeError):
        avg_price = 50

    if product_count == 0:
        return "Unknown"

    # Rough estimate: products × avg_price × 10 (assumed monthly sales)
    monthly_revenue = product_count * avg_price * 10

    if monthly_revenue < 1000:
        return "<$1K/mo"
    elif monthly_revenue < 10000:
        return "$1K-$10K/mo"
    elif monthly_revenue < 50000:
        return "$10K-$50K/mo"
    elif monthly_revenue < 100000:
        return "$50K-$100K/mo"
    else:
        return "$100K+/mo"


def _build_ecommerce_queries(query: str, location: str) -> List[str]:
    """Generate search queries for discovering ecommerce stores."""
    loc = f" {location}" if location else ""
    return [
        f'site:myshopify.com "{query}"{loc}',
        f'"powered by Shopify" "{query}"{loc}',
        f'"powered by WooCommerce" "{query}"{loc}',
        f'"powered by BigCommerce" "{query}"{loc}',
        f'{query} online store{loc}',
        f'{query} shop online{loc}',
        f'buy {query} online{loc}',
        f'{query} ecommerce{loc}',
        f'{query} store{loc}',
        f'{query} free shipping{loc}',
    ]
