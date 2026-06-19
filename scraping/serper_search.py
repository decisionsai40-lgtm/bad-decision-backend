"""
BAD DECISION — Serper.dev Google Search API Wrapper
====================================================
"""

import httpx
from typing import List, Dict, Any

from config import SERPER_API_KEY, SERPER_BASE_URL, SOURCE_TIMEOUT


async def serper_search(
    query: str,
    num_results: int = 20,
    search_type: str = "search",
    location: str = "",
) -> List[Dict[str, Any]]:
    """Search Google via Serper.dev. Returns clean JSON results."""
    if not SERPER_API_KEY:
        print("[SERPER] No API key configured — skipping")
        return []

    endpoint = f"{SERPER_BASE_URL}/{search_type}"

    payload: Dict[str, Any] = {
        "q": query,
        "num": min(num_results, 100),
        "gl": "us",
        "hl": "en",
    }

    if location:
        payload["location"] = location

    try:
        async with httpx.AsyncClient(timeout=SOURCE_TIMEOUT) as client:
            response = await client.post(
                endpoint,
                headers={
                    "X-API-KEY": SERPER_API_KEY,
                    "Content-Type": "application/json",
                },
                json=payload,
            )

            if response.status_code == 429:
                print("[SERPER] Rate limit hit (429)")
                return []

            if response.status_code == 401:
                print("[SERPER] Invalid API key (401)")
                return []

            if response.status_code != 200:
                print(f"[SERPER] Error {response.status_code}")
                return []

            data = response.json()

            if search_type == "maps":
                places = data.get("places", [])
                results = []
                for i, p in enumerate(places):
                    results.append({
                        "title": p.get("title", ""),
                        "link": p.get("website", ""),
                        "snippet": f"{p.get('address', '')} {p.get('phoneNumber', '')}".strip(),
                        "address": p.get("address", ""),
                        "phone": p.get("phoneNumber", ""),
                        "website": p.get("website", ""),
                        "rating": p.get("rating", 0),
                        "ratingCount": p.get("ratingCount", 0),
                        "category": p.get("category", ""),
                        "position": i,
                    })
                print(f"[SERPER] Maps: {len(results)} places for '{query[:50]}'")
                return results

            # Standard search
            organic = data.get("organic", [])
            results = []
            for i, item in enumerate(organic):
                results.append({
                    "title": item.get("title", ""),
                    "link": item.get("link", ""),
                    "snippet": item.get("snippet", ""),
                    "position": i,
                })

            print(f"[SERPER] {len(results)} results for '{query[:50]}'")
            return results

    except httpx.TimeoutException:
        print(f"[SERPER] Timeout: {query[:50]}")
        return []
    except Exception as e:
        print(f"[SERPER] Error: {e}")
        return []


async def serper_multi_search(queries: list, num_results: int = 10) -> list:
    """
    Run multiple Serper searches concurrently and merge results.
    Deduplicates by URL.
    """
    import asyncio

    tasks = [serper_search(q, num_results=num_results) for q in queries]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_results = []
    seen_urls = set()

    for r in results:
        if isinstance(r, list):
            for item in r:
                url = item.get("link", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_results.append(item)
                elif not url:
                    all_results.append(item)

    return all_results


async def serper_maps_search(query: str, location: str = "") -> list:
    """
    Search Google Maps via Serper.dev.
    Returns businesses with rating, reviews, phone, address, website.
    """
    return await serper_search(query, num_results=20, search_type="maps", location=location)


# ============================================================
# QUERY BUILDERS — generate 10 variations for each engine
# ============================================================

def build_smb_maps_queries(query: str, location: str) -> list:
    """Generate 10 Serper query variations for local business search."""
    return [
        f"{query} local business {location}",
        f"{query} near {location}",
        f"{query} directory {location}",
        f"{query} services {location}",
        f"{query} company {location}",
        f"best {query} {location}",
        f"top {query} {location}",
        f"{query} reviews {location}",
        f"{query} phone {location}",
        f"{query} address {location}",
    ]


def build_ads_intent_queries(query: str, location: str) -> list:
    """Generate 10 Serper query variations for ads intelligence."""
    return [
        f"{query} advertising {location}",
        f"{query} Facebook ads {location}",
        f"{query} Google ads {location}",
        f"{query} TikTok ads {location}",
        f"{query} marketing {location}",
        f"{query} sponsored {location}",
        f"{query} promoted {location}",
        f"{query} paid ads {location}",
        f"{query} ad campaign {location}",
        f"{query} running ads {location}",
    ]


def build_web_absent_queries(query: str, location: str) -> list:
    """Generate 10 Serper query variations for web-absent businesses."""
    return [
        f"{query} site:yelp.com {location}",
        f"{query} site:houzz.com {location}",
        f"{query} site:etsy.com {location}",
        f"{query} site:facebook.com {location}",
        f"{query} site:instagram.com {location}",
        f"{query} site:nextdoor.com {location}",
        f"{query} site:angi.com {location}",
        f"{query} site:thumbtack.com {location}",
        f"{query} site:bark.com {location}",
        f"{query} site:google.com/maps {location}",
    ]


def build_social_intent_queries(query: str, location: str) -> list:
    """Generate 10 Serper query variations for social intent."""
    return [
        f'{query} site:reddit.com "looking for" {location}',
        f'{query} site:reddit.com "need help" {location}',
        f'{query} site:reddit.com "recommendations" {location}',
        f'{query} site:twitter.com "looking for" {location}',
        f'{query} site:twitter.com "need help" {location}',
        f'{query} site:facebook.com "recommendations" {location}',
        f'{query} site:linkedin.com "hiring" {location}',
        f'{query} site:nextdoor.com "recommendations" {location}',
        f'"need a {query}" {location}',
        f'"looking for {query}" {location}',
    ]
