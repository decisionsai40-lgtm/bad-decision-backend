"""
BAD DECISION — OpenStreetMap (Nominatim + Overpass) Wrapper
=============================================================
OpenStreetMap provides free, open-source map data with excellent
European coverage. This module wraps:

  1. Nominatim — geocoding (convert "Lagos, Nigeria" → coordinates)
  2. Overpass API — query OSM for businesses by tag (amenity=cafe, shop=bakery)

This replaces Google Maps scraping (which requires JS and fails on Render free tier).

Why OSM:
  - Free, no API key needed (just a User-Agent header)
  - Very strong European coverage (Germany, France, UK, Italy, Spain)
  - Returns structured data (name, address, phone, website, opening_hours)
  - Rate limits: Nominatim 1 req/sec, Overpass 2 req/sec

Usage:
  coords = await geocode("Lagos, Nigeria")
  # coords = {"lat": 6.5244, "lon": 3.3792, "display_name": "Lagos, Nigeria"}

  businesses = await overpass_search("cafe", lat=6.5244, lon=3.3792, radius=5000)
  # businesses = [{"name": "...", "address": "...", "phone": "...", ...}, ...]
"""

import httpx
import asyncio
from typing import List, Dict, Any, Optional, Tuple
from urllib.parse import quote

from config import OSM_NOMINATIM_USER_AGENT, OSM_OVERPASS_ENDPOINT, SOURCE_TIMEOUT


# ============================================================
# NOMINATIM — Geocoding
# ============================================================
async def geocode(location: str) -> Optional[Dict[str, Any]]:
    """
    Convert a location string to coordinates using Nominatim.

    Args:
        location: A location string (e.g., "Lagos, Nigeria", "Paris, France")

    Returns:
        Dict with lat, lon, display_name — or None if not found.
    """
    if not location:
        return None

    try:
        async with httpx.AsyncClient(timeout=SOURCE_TIMEOUT) as client:
            response = await client.get(
                "https://nominatim.openstreetmap.org/search",
                params={
                    "q": location,
                    "format": "json",
                    "limit": 1,
                    "addressdetails": 1,
                },
                headers={"User-Agent": OSM_NOMINATIM_USER_AGENT},
            )

            if response.status_code != 200:
                print(f"[OSM-NOMINATIM] Error {response.status_code} for '{location}'")
                return None

            data = response.json()
            if not data:
                print(f"[OSM-NOMINATIM] No results for '{location}'")
                return None

            result = data[0]
            return {
                "lat": float(result.get("lat", 0)),
                "lon": float(result.get("lon", 0)),
                "display_name": result.get("display_name", location),
                "address": result.get("address", {}),
            }

    except Exception as e:
        print(f"[OSM-NOMINATIM] Error geocoding '{location}': {e}")
        return None


# ============================================================
# OVERPASS — Business Search
# ============================================================
# Common business category → OSM tag mappings
# This helps translate user queries (like "bakery", "gym") into OSM tags.
CATEGORY_TAGS = {
    "cafe": ["amenity=cafe"],
    "coffee": ["amenity=cafe"],
    "restaurant": ["amenity=restaurant"],
    "bar": ["amenity=bar"],
    "pub": ["amenity=pub"],
    "bakery": ["shop=bakery"],
    "butcher": ["shop=butcher"],
    "grocery": ["shop=supermarket", "shop=convenience"],
    "supermarket": ["shop=supermarket"],
    "convenience": ["shop=convenience"],
    "gym": ["leisure=fitness_centre", "leisure=sports_centre"],
    "fitness": ["leisure=fitness_centre", "leisure=sports_centre"],
    "salon": ["shop=hairdresser", "shop=beauty"],
    "barber": ["shop=hairdresser"],
    "hairdresser": ["shop=hairdresser"],
    "beauty": ["shop=beauty"],
    "spa": ["leisure=spa", "shop=beauty"],
    "pharmacy": ["amenity=pharmacy", "healthcare=pharmacy"],
    "dentist": ["amenity=dentist", "healthcare=dentist"],
    "doctor": ["amenity=doctors", "healthcare=doctor"],
    "clinic": ["amenity=clinic", "healthcare=clinic"],
    "hospital": ["amenity=hospital"],
    "veterinary": ["amenity=veterinary"],
    "vet": ["amenity=veterinary"],
    "car_repair": ["shop=car_repair", "craft=car_repair"],
    "auto_repair": ["shop=car_repair", "craft=car_repair"],
    "mechanic": ["shop=car_repair", "craft=car_repair"],
    "car_wash": ["amenity=car_wash"],
    "fuel": ["amenity=fuel"],
    "gas_station": ["amenity=fuel"],
    "bank": ["amenity=bank"],
    "atm": ["amenity=atm"],
    "clothing": ["shop=clothes"],
    "clothes": ["shop=clothes"],
    "shoe": ["shop=shoes"],
    "shoes": ["shop=shoes"],
    "jewelry": ["shop=jewelry"],
    "jeweller": ["shop=jewelry"],
    "florist": ["shop=florist"],
    "flowers": ["shop=florist"],
    "hardware": ["shop=hardware"],
    "electronics": ["shop=electronics"],
    "computer": ["shop=computer"],
    "mobile": ["shop=mobile_phone"],
    "phone": ["shop=mobile_phone"],
    "bookstore": ["shop=books"],
    "books": ["shop=books"],
    "toy": ["shop=toys"],
    "toys": ["shop=toys"],
    "furniture": ["shop=furniture"],
    "garden": ["shop=garden_centre"],
    "pet": ["shop=pet"],
    "optician": ["shop=optician"],
    "laundry": ["shop=laundry", "amenity=laundry"],
    "dry_cleaning": ["shop=dry_cleaning"],
    "tattoo": ["craft=tattoo"],
    "photographer": ["craft=photographer"],
    "plumber": ["craft=plumber"],
    "electrician": ["craft=electrician"],
    "carpenter": ["craft=carpenter"],
    "painter": ["craft=painter"],
    "roofer": ["craft=roofer"],
    "gardener": ["craft=gardener"],
}


def get_osm_tags_for_query(query: str) -> List[str]:
    """
    Try to match the user's query to OSM tags.
    Falls back to a generic name search if no category match.
    """
    query_lower = query.lower().strip()

    # Try exact category match
    if query_lower in CATEGORY_TAGS:
        return CATEGORY_TAGS[query_lower]

    # Try partial match (e.g., "coffee shop" → "coffee")
    for keyword, tags in CATEGORY_TAGS.items():
        if keyword in query_lower:
            return tags

    # No match — return empty (caller can do a name search instead)
    return []


async def overpass_search(
    query: str,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    radius: int = 10000,
    limit: int = 30,
) -> List[Dict[str, Any]]:
    """
    Search OpenStreetMap for businesses matching the query.

    Args:
        query: Business type or name (e.g., "cafe", "bakery", "roofing")
        lat: Center latitude (if None, searches globally — slower)
        lon: Center longitude
        radius: Search radius in meters (default 10km)
        limit: Max results

    Returns:
        List of business dicts with name, address, phone, website, etc.
    """
    tags = get_osm_tags_for_query(query)

    # Build the Overpass QL query
    if tags:
        # Search by OSM tags (e.g., amenity=cafe)
        tag_filters = []
        for tag in tags:
            key, value = tag.split("=", 1)
            tag_filters.append(f'node["{key}"="{value}"](around:{radius},{lat or 0},{lon or 0});')
            tag_filters.append(f'way["{key}"="{value}"](around:{radius},{lat or 0},{lon or 0});')

        overpass_query = f"[out:json][timeout:25];({''.join(tag_filters)});out center 30;"
    else:
        # Fallback: search by name (slower, less precise)
        safe_name = query.replace('"', '\\"')
        overpass_query = f'[out:json][timeout:25];(node["name"~"{safe_name}",i](around:{radius},{lat or 0},{lon or 0});way["name"~"{safe_name}",i](around:{radius},{lat or 0},{lon or 0}););out center 30;'

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                OSM_OVERPASS_ENDPOINT,
                data={"data": overpass_query},
                headers={"User-Agent": OSM_NOMINATIM_USER_AGENT},
            )

            if response.status_code == 429:
                print("[OSM-OVERPASS] Rate limit hit (429) — too many requests")
                return []

            if response.status_code != 200:
                print(f"[OSM-OVERPASS] Error {response.status_code}")
                return []

            data = response.json()
            elements = data.get("elements", [])

            businesses = []
            for elem in elements[:limit]:
                tags_data = elem.get("tags", {})

                # Build address from OSM address components
                addr_parts = []
                for field in ["addr:housenumber", "addr:street", "addr:city", "addr:state", "addr:postcode", "addr:country"]:
                    val = tags_data.get(field)
                    if val:
                        addr_parts.append(val)
                address = ", ".join(addr_parts) if addr_parts else ""

                business = {
                    "name": tags_data.get("name", ""),
                    "address": address,
                    "phone": tags_data.get("phone", tags_data.get("contact:phone", tags_data.get("addr:phone", ""))),
                    "website": tags_data.get("website", tags_data.get("contact:website", tags_data.get("url", ""))),
                    "email": tags_data.get("email", tags_data.get("contact:email", "")),
                    "opening_hours": tags_data.get("opening_hours", ""),
                    "lat": elem.get("lat", elem.get("center", {}).get("lat", 0)),
                    "lon": elem.get("lon", elem.get("center", {}).get("lon", 0)),
                    "category": tags_data.get("amenity", tags_data.get("shop", tags_data.get("craft", tags_data.get("leisure", "")))),
                    "source": "openstreetmap",
                }

                if business["name"]:
                    businesses.append(business)

            print(f"[OSM-OVERPASS] Found {len(businesses)} businesses for '{query}' (tags: {tags or 'name search'})")
            return businesses

    except httpx.TimeoutException:
        print(f"[OSM-OVERPASS] Timeout for '{query}'")
        return []

    except Exception as e:
        print(f"[OSM-OVERPASS] Error: {e}")
        return []


async def search_local_businesses(
    query: str,
    location: str = "",
    radius: int = 10000,
    limit: int = 30,
) -> List[Dict[str, Any]]:
    """
    High-level function: geocode the location, then search Overpass for businesses.

    Args:
        query: Business type (e.g., "cafe", "bakery", "gym")
        location: Location string (e.g., "Lagos, Nigeria")
        radius: Search radius in meters
        limit: Max results

    Returns:
        List of business dicts
    """
    lat, lon = None, None

    if location:
        coords = await geocode(location)
        if coords:
            lat = coords["lat"]
            lon = coords["lon"]
            print(f"[OSM] Geocoded '{location}' to {lat}, {lon}")

    # Rate limit: Nominatim requires max 1 req/sec. We already made 1 geocode call,
    # so wait 1 second before the Overpass call to be safe.
    if location:
        await asyncio.sleep(1)

    businesses = await overpass_search(query, lat=lat, lon=lon, radius=radius, limit=limit)
    return businesses
