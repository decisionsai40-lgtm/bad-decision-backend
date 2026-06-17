"""
BAD DECISION — Serper.dev Google Search API Wrapper
====================================================
Serper.dev returns Google search results as clean JSON. No JS rendering
needed. This is the PRIMARY search backend for all Google queries.

Why Serper.dev instead of scraping Google directly:
  - Scrapling's Fetcher mode cannot execute JavaScript.
  - Google Search, Google Maps, Facebook Ads Library all require JS.
  - Serper.dev runs Google Search server-side and returns clean JSON.
  - Free tier: 2,500 searches/month. Paid: $50/month for 10,000 searches.

Usage:
  results = await serper_search("roofers in Texas running ads")
  # results = [{"title": "...", "link": "...", "snippet": "..."}, ...]
"""

import httpx
from typing import List, Dict, Any, Optional

from config import SERPER_API_KEY, SERPER_BASE_URL, SOURCE_TIMEOUT


async def serper_search(
    query: str,
    num_results: int = 20,
    search_type: str = "search",
    location: str = "",
) -> List[Dict[str, Any]]:
    """
    Search Google via Serper.dev. Returns clean JSON results.

    Args:
        query: The search query string
        num_results: How many results to return (max 100)
        search_type: "search" (web), "maps" (local), "images", "news"
        location: Optional location string (e.g., "Texas, United States")

    Returns:
        List of result dicts with keys: title, link, snippet, position
    """
    if not SERPER_API_KEY:
        print("[SERPER] No API key configured — skipping Serper search")
        return []

    # Determine endpoint based on search type
    endpoint = f"{SERPER_BASE_URL}/{search_type}" if search_type != "search" else SERPER_BASE_URL

    payload: Dict[str, Any] = {
        "q": query,
        "num": min(num_results, 100),
        "gl": "us",  # Default to US
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
                print("[SERPER] Rate limit hit (429) — consider upgrading plan")
                return []

            if response.status_code == 401:
                print("[SERPER] Invalid API key (401)")
                return []

            if response.status_code != 200:
                print(f"[SERPER] Error {response.status_code}: {response.text[:200]}")
                return []

            data = response.json()

            # Serper returns different formats for different search types
            if search_type == "maps":
                # Maps results have "places" array
                places = data.get("places", [])
                return [
                    {
                        "title": p.get("title", ""),
                        "link": p.get("website", ""),
                        "snippet": f"{p.get('address', '')} {p.get('phoneNumber', '')}".strip(),
                        "address": p.get("address", ""),
                        "phone": p.get("phoneNumber", ""),
                        "rating": p.get("rating", 0),
                        "position": i,
                    }
                    for i, p in enumerate(places)
                ]

            # Standard search results
            organic = data.get("organic", [])
            results = []
            for i, item in enumerate(organic):
                results.append({
                    "title": item.get("title", ""),
                    "link": item.get("link", ""),
                    "snippet": item.get("snippet", ""),
                    "position": i,
                })

            # Also include "knowledgeGraph" if present (often has company info)
            kg = data.get("knowledgeGraph")
            if kg:
                results.insert(0, {
                    "title": kg.get("title", ""),
                    "link": kg.get("website", ""),
                    "snippet": kg.get("description", ""),
                    "position": -1,
                    "source": "knowledge_graph",
                })

            print(f"[SERPER] Found {len(results)} results for '{query[:50]}...'")
            return results

    except httpx.TimeoutException:
        print(f"[SERPER] Timeout for query: {query[:50]}...")
        return []

    except Exception as e:
        print(f"[SERPER] Error: {e}")
        return []


async def serper_search_batch(
    queries: List[str],
    num_results: int = 10,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Run multiple Serper searches concurrently using asyncio.gather.
    Returns a dict mapping query → results.

    Args:
        queries: List of query strings
        num_results: Results per query

    Returns:
        Dict mapping each query to its list of results
    """
    import asyncio

    async def _safe_search(q: str) -> tuple:
        results = await serper_search(q, num_results=num_results)
        return (q, results)

    pairs = await asyncio.gather(*[_safe_search(q) for q in queries], return_exceptions=True)

    output = {}
    for pair in pairs:
        if isinstance(pair, Exception):
            print(f"[SERPER-BATCH] Error: {pair}")
            continue
        query, results = pair
        output[query] = results

    return output


def build_serper_query_for_ads(query: str) -> str:
    """Build a Serper query optimized for finding businesses running ads."""
    return f"{query} advertising ads"


def build_serper_query_for_social(query: str) -> str:
    """Build a Serper query for finding social intent posts."""
    return f'{query} site:reddit.com OR site:twitter.com "looking for" OR "need help" OR "hiring"'


def build_serper_query_for_aggregators(query: str) -> str:
    """Build a Serper query for finding businesses on aggregator sites."""
    return f"{query} site:yelp.com OR site:houzz.com OR site:etsy.com"
