"""
BAD DECISION AI — Serper.dev API Client
========================================
FREE: 2,500 queries on signup, no credit card.
Returns Google Search + Maps results as structured JSON.

V3: Supports gl (geo-location) parameter mapped from country codes
    for geo-targeted search results.
"""

import httpx
from typing import List, Dict, Any, Optional
from config import SERPER_API_KEY


SERPER_SEARCH_URL = "https://google.serper.dev/search"
SERPER_MAPS_URL = "https://google.serper.dev/maps"
SERPER_PLACES_URL = "https://google.serper.dev/places"

# ============================================================
# COUNTRY TO GL MAPPING — Maps ISO country codes to Google's
# geolocation parameter values for targeted search results
# ============================================================
COUNTRY_TO_GL = {
    # Africa
    "NG": "ng",    # Nigeria
    "ZA": "za",    # South Africa
    "KE": "ke",    # Kenya
    "GH": "gh",    # Ghana
    "EG": "eg",    # Egypt
    "TZ": "tz",    # Tanzania
    "ET": "et",    # Ethiopia
    "MA": "ma",    # Morocco
    # Europe
    "GB": "uk",    # United Kingdom
    "DE": "de",    # Germany
    "FR": "fr",    # France
    "IT": "it",    # Italy
    "ES": "es",    # Spain
    "NL": "nl",    # Netherlands
    "PT": "pt",    # Portugal
    "PL": "pl",    # Poland
    "IE": "ie",    # Ireland
    "SE": "se",    # Sweden
    # Americas
    "US": "us",    # United States
    "CA": "ca",    # Canada
    "BR": "br",    # Brazil
    "MX": "mx",    # Mexico
    "AR": "ar",    # Argentina
    "CO": "co",    # Colombia
    # Asia
    "IN": "in",    # India
    "CN": "cn",    # China
    "JP": "jp",    # Japan
    "KR": "kr",    # South Korea
    "SG": "sg",    # Singapore
    "AE": "ae",    # UAE
    "SA": "sa",    # Saudi Arabia
    "IL": "il",    # Israel
    # Oceania
    "AU": "au",    # Australia
    "NZ": "nz",    # New Zealand
}


def _resolve_gl(gl: str, location: Optional[dict] = None) -> str:
    """
    Resolve the gl parameter from either a direct value or a location dict.
    If a location dict with a country code is provided, it takes precedence.
    """
    if location and location.get("country"):
        country_gl = COUNTRY_TO_GL.get(location.get("country", "").upper())
        if country_gl:
            return country_gl
    return gl


async def serper_search(
    query: str,
    num_results: int = 20,
    gl: str = "us",
    location: Optional[dict] = None,
) -> List[Dict[str, Any]]:
    """Search Google via Serper.dev and return structured results."""
    if not SERPER_API_KEY:
        print("[SERPER] No API key configured — skipping")
        return []

    # Resolve gl from location dict if provided
    resolved_gl = _resolve_gl(gl, location)

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                SERPER_SEARCH_URL,
                headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
                json={"q": query, "num": num_results, "gl": resolved_gl},
            )

            if response.status_code != 200:
                print(f"[SERPER] Search error: {response.status_code}")
                return []

            data = response.json()
            results = []

            for item in data.get("organic", []):
                results.append({
                    "title": item.get("title", ""),
                    "link": item.get("link", ""),
                    "snippet": item.get("snippet", ""),
                    "position": item.get("position", 0),
                })

            return results

    except Exception as e:
        print(f"[SERPER] Search error: {e}")
        return []


async def serper_maps(
    query: str,
    num_results: int = 20,
    gl: str = "us",
    location: Optional[dict] = None,
) -> List[Dict[str, Any]]:
    """Search Google Maps via Serper.dev and return structured business data."""
    if not SERPER_API_KEY:
        print("[SERPER] No API key configured — skipping")
        return []

    # Resolve gl from location dict if provided
    resolved_gl = _resolve_gl(gl, location)

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                SERPER_MAPS_URL,
                headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
                json={"q": query, "num": num_results, "gl": resolved_gl},
            )

            if response.status_code != 200:
                print(f"[SERPER] Maps error: {response.status_code}")
                return []

            data = response.json()
            results = []

            for item in data.get("places", []):
                results.append({
                    "company_name": item.get("title", ""),
                    "address": item.get("address", "ABSENT"),
                    "phone": item.get("phone", "ABSENT"),
                    "website": item.get("website", "ABSENT"),
                    "rating": item.get("rating"),
                    "reviews": item.get("reviews"),
                    "category": item.get("category", "ABSENT"),
                    "latitude": item.get("latitude"),
                    "longitude": item.get("longitude"),
                    "discovery_source": "serper",
                })

            print(f"[SERPER] Maps: Found {len(results)} businesses for '{query}' (gl={resolved_gl})")
            return results

    except Exception as e:
        print(f"[SERPER] Maps error: {e}")
        return []


async def serper_site_search(
    site: str,
    query: str,
    num_results: int = 10,
    location: Optional[dict] = None,
) -> List[Dict[str, Any]]:
    """Search a specific site via Serper.dev (e.g., site:facebook.com)."""
    if not SERPER_API_KEY:
        return []

    full_query = f"site:{site} {query}"
    return await serper_search(full_query, num_results=num_results, location=location)
