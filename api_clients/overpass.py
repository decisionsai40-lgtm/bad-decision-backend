"""
BAD DECISION AI — Overpass API Client (OpenStreetMap)
=====================================================
Free, unlimited API for finding businesses on OpenStreetMap.
Supports location-based queries and no-website filtering.
"""

import httpx
from typing import List, Dict, Any
import asyncio


async def search_businesses(query: str, location: str = "") -> List[Dict[str, Any]]:
    """Search for businesses on OpenStreetMap using Overpass API."""
    overpass_url = "https://overpass-api.de/api/interpreter"

    # Build Overpass QL query
    area_filter = ""
    if location:
        area_filter = f'area[name="{location}"]->.searchArea;'

    query_parts = query.lower().split()

    # Search for common business types
    overpass_query = f"""
    [out:json][timeout:30];
    {area_filter}
    (
      node["shop"~"{'|'.join(query_parts)}"[]{"name"}]({'.searchArea;' if location else ''});
      node["amenity"~"restaurant|cafe|bar|fast_food|pharmacy|dentist|doctors|veterinary"]["name"]({'.searchArea;' if location else ''});
      node["office"~"insurance|estate_agent|lawyer|accountant"]["name"]({'.searchArea;' if location else ''});
      way["shop"~"{'|'.join(query_parts)}"[]{"name"}]({'.searchArea;' if location else ''});
      way["amenity"~"restaurant|cafe|bar|fast_food|pharmacy|dentist|doctors|veterinary"]["name"]({'.searchArea;' if location else ''});
    );
    out center body 50;
    """

    try:
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(overpass_url, data={"data": overpass_query})

            if response.status_code == 200:
                data = response.json()
                businesses = []

                for element in data.get("elements", []):
                    tags = element.get("tags", {})
                    name = tags.get("name", "")
                    if not name:
                        continue

                    address = _format_address(tags)
                    business = {
                        "name": name,
                        "company_name": name,
                        "website_url": tags.get("website", tags.get("contact:website", "ABSENT")),
                        "address": address,
                        "phone": tags.get("phone", tags.get("contact:phone", "ABSENT")),
                        "type": tags.get("shop", tags.get("amenity", tags.get("office", "business"))),
                        "lat": element.get("lat", element.get("center", {}).get("lat")),
                        "lon": element.get("lon", element.get("center", {}).get("lon")),
                    }
                    businesses.append(business)

                return businesses[:30]
            else:
                print(f"[OVERPASS] HTTP {response.status_code}")
                return []

    except Exception as e:
        print(f"[OVERPASS] Error: {e}")
        return []


async def search_businesses_no_website(query: str, location: str = "") -> List[Dict[str, Any]]:
    """Search for businesses WITHOUT a website tag on OpenStreetMap."""
    overpass_url = "https://overpass-api.de/api/interpreter"

    area_filter = ""
    if location:
        area_filter = f'area[name="{location}"]->.searchArea;'

    overpass_query = f"""
    [out:json][timeout:30];
    {area_filter}
    (
      node["shop"]["name"]["website"!~"."]({'.searchArea;' if location else ''});
      node["amenity"~"restaurant|cafe|bar|fast_food|pharmacy|dentist|doctors|veterinary"]["name"]["website"!~"."]({'.searchArea;' if location else ''});
      node["office"]["name"]["website"!~"."]({'.searchArea;' if location else ''});
      way["shop"]["name"]["website"!~"."]({'.searchArea;' if location else ''});
      way["amenity"~"restaurant|cafe|bar|fast_food|pharmacy|dentist|doctors|veterinary"]["name"]["website"!~"."]({'.searchArea;' if location else ''});
    );
    out center body 50;
    """

    try:
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(overpass_url, data={"data": overpass_query})

            if response.status_code == 200:
                data = response.json()
                businesses = []

                for element in data.get("elements", []):
                    tags = element.get("tags", {})
                    name = tags.get("name", "")
                    if not name:
                        continue

                    business = {
                        "name": name,
                        "company_name": name,
                        "website_url": "ABSENT",
                        "address": _format_address(tags),
                        "phone": tags.get("phone", tags.get("contact:phone", "ABSENT")),
                        "type": tags.get("shop", tags.get("amenity", tags.get("office", "business"))),
                        "aggregator_source": "OpenStreetMap",
                        "has_external_website": False,
                    }
                    businesses.append(business)

                return businesses[:30]
            else:
                return []

    except Exception as e:
        print(f"[OVERPASS] Error: {e}")
        return []


def _format_address(tags: dict) -> str:
    """Format OSM tags into a readable address."""
    parts = []
    if tags.get("addr:housenumber"):
        parts.append(tags["addr:housenumber"])
    if tags.get("addr:street"):
        parts.append(tags["addr:street"])
    if tags.get("addr:city"):
        parts.append(tags["addr:city"])
    if tags.get("addr:state"):
        parts.append(tags["addr:state"])

    return ", ".join(parts) if parts else "ABSENT"
