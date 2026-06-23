"""
BAD DECISION — Serper.dev Google Search API Wrapper
====================================================
"""

import httpx
from typing import List, Dict, Any

from config import SERPER_API_KEY, SERPER_BASE_URL, SOURCE_TIMEOUT
from scraping.location_mapper import get_country_name


# Map ISO country codes → Serper gl (geolocation) codes.
# Serper uses ISO 3166-1 alpha-2 codes for gl — same as our country codes.
# But we also need to set the right language code (hl).
COUNTRY_LOCALE = {
    "US": ("us", "en"), "CA": ("ca", "en"), "GB": ("uk", "en"),
    "AU": ("au", "en"), "NG": ("ng", "en"), "ZA": ("za", "en"),
    "KE": ("ke", "en"), "GH": ("gh", "en"), "IN": ("in", "en"),
    "PK": ("pk", "en"), "BD": ("bd", "en"), "DE": ("de", "de"),
    "FR": ("fr", "fr"), "ES": ("es", "es"), "IT": ("it", "it"),
    "NL": ("nl", "nl"), "AE": ("ae", "en"), "SA": ("sa", "ar"),
    "JP": ("jp", "ja"), "KR": ("kr", "ko"), "CN": ("cn", "zh"),
    "BR": ("br", "pt"), "MX": ("mx", "es"), "RU": ("ru", "ru"),
    "TR": ("tr", "tr"), "EG": ("eg", "ar"), "SG": ("sg", "en"),
    "MY": ("my", "en"), "PH": ("ph", "en"), "TH": ("th", "th"),
    "ID": ("id", "en"), "VN": ("vn", "vi"), "NZ": ("nz", "en"),
    "IE": ("ie", "en"), "SE": ("se", "sv"), "NO": ("no", "no"),
    "DK": ("dk", "da"), "FI": ("fi", "fi"), "PL": ("pl", "pl"),
    "PT": ("pt", "pt"), "GR": ("gr", "el"), "CZ": ("cz", "cs"),
    "AR": ("ar", "es"), "CL": ("cl", "es"), "CO": ("co", "es"),
    "PE": ("pe", "es"), "CH": ("ch", "de"), "AT": ("at", "de"),
    "BE": ("be", "nl"), "UA": ("ua", "uk"), "RO": ("ro", "ro"),
    "HU": ("hu", "hu"), "IL": ("il", "en"), "QA": ("qa", "ar"),
    "KW": ("kw", "ar"), "BH": ("bh", "ar"), "OM": ("om", "ar"),
    "JO": ("jo", "ar"), "LB": ("lb", "ar"), "MA": ("ma", "ar"),
    "TN": ("tn", "ar"), "DZ": ("dz", "ar"),
}


def _get_locale(country_code: str) -> tuple:
    """Return (gl, hl) for Serper based on country code."""
    if not country_code:
        return ("us", "en")
    code = country_code.upper().strip()
    return COUNTRY_LOCALE.get(code, (code.lower(), "en"))


async def serper_search(
    query: str,
    num_results: int = 20,
    search_type: str = "search",
    location: str = "",
    country_code: str = "",
) -> List[Dict[str, Any]]:
    """Search Google via Serper.dev. Returns clean JSON results.

    Args:
        query: Search query
        num_results: Max results to return
        search_type: 'search' (default) or 'maps'
        location: Full location string (e.g. "Lagos, Nigeria")
        country_code: ISO country code (e.g. "NG") — used to set gl/hl
    """
    if not SERPER_API_KEY:
        print("[SERPER] No API key configured — skipping")
        return []

    endpoint = f"{SERPER_BASE_URL}/{search_type}"

    # Determine geolocation (gl) and language (hl) from country code.
    # This is CRITICAL: without gl="ng", Serper returns US results
    # even when location="Lagos, Nigeria" is set.
    gl, hl = _get_locale(country_code)

    payload: Dict[str, Any] = {
        "q": query,
        "num": min(num_results, 100),
        "gl": gl,
        "hl": hl,
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
                    # Extract website URL — Serper maps returns it under "website"
                    website = p.get("website", "") or p.get("site", "") or ""
                    # Clean up the website URL
                    if website and not website.startswith("http"):
                        website = "https://" + website
                    results.append({
                        "title": p.get("title", ""),
                        "link": website,
                        "snippet": f"{p.get('address', '')} {p.get('phoneNumber', '')}".strip(),
                        "address": p.get("address", ""),
                        "phone": p.get("phoneNumber", ""),
                        "website": website,
                        "rating": p.get("rating", 0),
                        "ratingCount": p.get("ratingCount", 0),
                        "category": p.get("category", "") or p.get("types", ""),
                        "position": i,
                    })
                print(f"[SERPER] Maps: {len(results)} places for '{query[:50]}' (gl={gl}, loc={location})")
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

            print(f"[SERPER] {len(results)} results for '{query[:50]}' (gl={gl})")
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


async def serper_maps_search(query: str, location: str = "", country_code: str = "") -> list:
    """
    Search Google Maps via Serper.dev.
    Returns businesses with rating, reviews, phone, address, website.
    """
    return await serper_search(query, num_results=20, search_type="maps", location=location, country_code=country_code)


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
