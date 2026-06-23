"""
BAD DECISION — Google Maps Bulk Data Extraction
================================================
Uses Outscraper API to extract business data from Google Maps.
Returns up to 500 businesses per query.
Free tier: 500 businesses per month. Then $3 per 1,000 records.

Usage:
    businesses = await scrape_google_maps("roofers", "Dallas, TX", limit=200)
"""
import httpx
import os
import asyncio
from typing import List, Dict, Any, Optional

OUTSCRAPER_API_KEY = os.getenv("OUTSCRAPER_API_KEY", "").strip()
OUTSCRAPER_BASE_URL = "https://api.app.outscraper.com"
SOURCE_TIMEOUT = 30  # Reduced from 60 — was causing search timeouts


async def scrape_google_maps(
    query: str,
    location: str = "",
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """
    Extract business data from Google Maps via Outscraper.

    Args:
        query: What to search for (e.g., "roofers", "dentists")
        location: Where to search (e.g., "Dallas, TX")
        limit: Max number of businesses to return

    Returns:
        List of business dicts with: name, address, phone, website, rating,
        reviews, category, opening_hours, price_level
    """
    if not OUTSCRAPER_API_KEY:
        print("[OUTSCRAPER] No API key configured, skipping")
        return []

    search_query = f"{query} {location}".strip()

    try:
        async with httpx.AsyncClient(timeout=SOURCE_TIMEOUT) as client:
            # Outscraper Google Maps search endpoint
            # Docs: https://outscraper.com/docs/#google-maps
            # Try the primary endpoint first, fall back to alternatives on 404
            endpoints_to_try = [
                f"{OUTSCRAPER_BASE_URL}/google-maps-search",
                f"{OUTSCRAPER_BASE_URL}/google-maps",
            ]

            response = None
            for endpoint in endpoints_to_try:
                response = await client.post(
                    endpoint,
                    headers={
                        "X-API-KEY": OUTSCRAPER_API_KEY,
                        "Content-Type": "application/json",
                    },
                    json={
                        "query": search_query,
                        "limit": min(limit, 500),
                        "async": False,  # Synchronous mode (wait for results)
                    },
                )
                if response.status_code == 200:
                    break
                if response.status_code == 404:
                    print(f"[OUTSCRAPER] 404 on {endpoint}, trying next...")
                    continue
                # For other errors (403, 429, 500), don't retry
                break

            if response is None:
                return []

            if response.status_code == 429:
                print("[OUTSCRAPER] Rate limit hit (429)")
                return []
            if response.status_code == 403:
                print("[OUTSCRAPER] Invalid API key (403)")
                return []
            if response.status_code != 200:
                print(f"[OUTSCRAPER] Error {response.status_code}: {response.text[:200]}")
                return []

            data = response.json()

            # Outscraper returns results in different formats depending on the endpoint.
            # The /google-maps-search endpoint returns: { "data": [...] } or just [...]
            # Each item has fields like: name, site, phone, address, rating, reviews, category
            if isinstance(data, dict):
                results = data.get("data", data.get("results", []))
            elif isinstance(data, list):
                # Sometimes the response is a list with a single object containing the actual results
                if len(data) == 1 and isinstance(data[0], dict) and "data" in data[0]:
                    results = data[0]["data"]
                else:
                    results = data
            else:
                results = []

            businesses = []
            for item in results[:limit]:
                business = {
                    "company_name": item.get("site", item.get("name", "")),
                    "website_url": item.get("site", item.get("website", "ABSENT")),
                    "phone": item.get("phone", "ABSENT"),
                    "address": item.get("address", "ABSENT"),
                    "rating": float(item.get("rating", 0) or 0),
                    "review_count": int(item.get("reviews", item.get("rating_count", 0)) or 0),
                    "category": item.get("category", "ABSENT"),
                    "opening_hours": item.get("opening_hours", "ABSENT"),
                    "price_level": item.get("price_level", "ABSENT"),
                }

                # Fix website URL
                if business["website_url"] and business["website_url"] != "ABSENT":
                    if not business["website_url"].startswith("http"):
                        business["website_url"] = "https://" + business["website_url"]
                else:
                    business["website_url"] = "ABSENT"

                # Use the name field if site is empty
                if not business["company_name"]:
                    business["company_name"] = item.get("name", "Unknown")

                businesses.append(business)

            print(f"[OUTSCRAPER] Found {len(businesses)} businesses for '{search_query[:50]}'")
            return businesses

    except httpx.TimeoutException:
        print(f"[OUTSCRAPER] Timeout for '{search_query[:50]}'")
        return []
    except Exception as e:
        print(f"[OUTSCRAPER] Error: {e}")
        return []


def build_geo_split_queries(query: str, location: str) -> List[str]:
    """
    Generate geographic sub-region queries to multiply results.
    Instead of one query 'roofers in Dallas', generate:
    - roofers in North Dallas
    - roofers in South Dallas
    - roofers in East Dallas
    - roofers in West Dallas
    - roofers in Downtown Dallas
    - roofers in Dallas suburbs

    Returns the original query plus 5 sub-region queries.
    """
    if not location:
        return [query]

    base = f"{query} in {location}"
    return [
        base,
        f"{query} in North {location}",
        f"{query} in South {location}",
        f"{query} in East {location}",
        f"{query} in West {location}",
        f"{query} in Downtown {location}",
    ]
