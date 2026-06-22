"""
BAD DECISION — Yelp Business Directory API
============================================
Free API, 5,000 requests per day, 240 businesses per search.
Returns: name, phone, address, rating, review count, categories, URL.

Usage:
    businesses = await search_yelp("roofers", "Dallas, TX", limit=50)
"""
import httpx
import os
from typing import List, Dict, Any

YELP_FUSION_API_KEY = os.getenv("YELP_FUSION_API_KEY", "").strip()
YELP_API_BASE = "https://api.yelp.com/v3"
SOURCE_TIMEOUT = 15


async def search_yelp(
    query: str,
    location: str = "",
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """
    Search Yelp for businesses matching the query.

    Args:
        query: What to search for (e.g., "roofers")
        location: Where to search (e.g., "Dallas, TX")
        limit: Max results (Yelp caps at 50 per request)

    Returns:
        List of business dicts with: company_name, phone, address,
        rating, review_count, categories, yelp_url
    """
    if not YELP_FUSION_API_KEY:
        print("[YELP] No API key configured, skipping")
        return []

    if not location:
        location = "United States"

    try:
        async with httpx.AsyncClient(timeout=SOURCE_TIMEOUT) as client:
            response = await client.get(
                f"{YELP_API_BASE}/businesses/search",
                headers={"Authorization": f"Bearer {YELP_FUSION_API_KEY}"},
                params={
                    "term": query,
                    "location": location,
                    "limit": min(limit, 50),
                    "sort_by": "best_match",
                },
            )

            if response.status_code == 429:
                print("[YELP] Rate limit hit (429)")
                return []

            if response.status_code != 200:
                print(f"[YELP] Error {response.status_code}: {response.text[:200]}")
                return []

            data = response.json()
            raw_businesses = data.get("businesses", [])

            businesses = []
            for item in raw_businesses[:limit]:
                # Build address from components
                addr_parts = []
                loc = item.get("location", {})
                if loc.get("address1"):
                    addr_parts.append(loc["address1"])
                if loc.get("city"):
                    addr_parts.append(loc["city"])
                if loc.get("state"):
                    addr_parts.append(loc["state"])
                if loc.get("zip_code"):
                    addr_parts.append(loc["zip_code"])
                address = ", ".join(addr_parts) if addr_parts else "ABSENT"

                # Build categories list
                categories = [c.get("title", "") for c in item.get("categories", []) if c.get("title")]
                category_str = ", ".join(categories) if categories else "ABSENT"

                business = {
                    "company_name": item.get("name", ""),
                    "website_url": "ABSENT",  # Yelp doesn't return website in search
                    "phone": item.get("phone", "ABSENT"),
                    "address": address,
                    "rating": float(item.get("rating", 0) or 0),
                    "review_count": int(item.get("review_count", 0) or 0),
                    "category": category_str,
                    "yelp_url": item.get("url", "ABSENT"),
                    "yelp_rating": float(item.get("rating", 0) or 0),
                    "yelp_categories": categories,
                }
                businesses.append(business)

            print(f"[YELP] Found {len(businesses)} businesses for '{query}' in '{location}'")
            return businesses

    except httpx.TimeoutException:
        print(f"[YELP] Timeout for '{query}' in '{location}'")
        return []
    except Exception as e:
        print(f"[YELP] Error: {e}")
        return []
