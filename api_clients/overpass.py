"""
BAD DECISION AI — Overpass API Client (OpenStreetMap)
=====================================================
FREE, unlimited, no API key needed.
Finds businesses by category and location with structured data.

V3: Supports location dict with continent/country/region for
    better Overpass area filtering using bounding boxes.
"""

import httpx
from typing import List, Dict, Any, Optional
from urllib.parse import quote_plus


OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Mapping common search terms to OSM tags
OSM_TAG_MAP = {
    "lawyer": ["office=lawyer", "office=attorney"],
    "attorney": ["office=lawyer", "office=attorney"],
    "dentist": ["amenity=dentist", "healthcare=dentist"],
    "doctor": ["amenity=doctors", "healthcare=doctor"],
    "restaurant": ["amenity=restaurant"],
    "cafe": ["amenity=cafe"],
    "hotel": ["tourism=hotel"],
    "gym": ["leisure=fitness_centre"],
    "coach": ["office=consulting"],
    "coaching": ["office=consulting"],
    "plumber": ["craft=plumber", "office=plumber"],
    "electrician": ["craft=electrician"],
    "roofer": ["craft=roofer", "office=roofer"],
    "contractor": ["office=contractor", "craft=contractor"],
    "realtor": ["office=estate_agent", "office=realtor"],
    "real estate": ["office=estate_agent", "office=realtor"],
    "accountant": ["office=accountant"],
    "insurance": ["office=insurance"],
    "architect": ["office=architect"],
    "vet": ["amenity=veterinary"],
    "pharmacy": ["amenity=pharmacy"],
    "hairdresser": ["shop=hairdresser"],
    "beauty": ["shop=beauty", "shop=cosmetics"],
    "car repair": ["shop=car_repair"],
    "garage": ["shop=car_repair"],
    "church": ["amenity=place_of_worship", "building=church"],
    "school": ["amenity=school"],
    "barber": ["shop=hairdresser"],
}

# ============================================================
# COUNTRY BOUNDING BOXES — For better Overpass queries
# Maps ISO country codes to (south, west, north, east) bounding boxes
# ============================================================
COUNTRY_BOUNDING_BOXES = {
    # Africa
    "NG": (4.0, 2.5, 14.0, 15.0),           # Nigeria
    "ZA": (-35.0, 16.0, -22.0, 33.0),        # South Africa
    "KE": (-5.0, 33.0, 5.0, 42.0),           # Kenya
    "GH": (4.5, -3.5, 11.5, 1.5),            # Ghana
    "EG": (22.0, 24.0, 31.5, 37.0),          # Egypt
    "TZ": (-11.0, 29.0, -1.0, 41.0),         # Tanzania
    "ET": (3.0, 33.0, 15.0, 48.0),           # Ethiopia
    "MA": (27.0, -13.0, 36.0, -1.0),         # Morocco
    # Europe
    "GB": (49.0, -8.0, 61.0, 2.0),           # United Kingdom
    "DE": (47.0, 6.0, 55.0, 15.0),           # Germany
    "FR": (42.0, -5.0, 51.0, 10.0),          # France
    "IT": (36.0, 7.0, 47.0, 19.0),           # Italy
    "ES": (36.0, -10.0, 44.0, 4.0),          # Spain
    "NL": (50.5, 3.0, 54.0, 7.5),            # Netherlands
    "PT": (37.0, -10.0, 42.0, -6.0),         # Portugal
    "PL": (49.0, 14.0, 55.0, 24.0),          # Poland
    "IE": (51.0, -11.0, 55.5, -5.5),         # Ireland
    "SE": (55.0, 11.0, 69.0, 24.0),          # Sweden
    # Americas
    "US": (24.0, -125.0, 49.0, -66.0),       # United States
    "CA": (42.0, -141.0, 84.0, -52.0),       # Canada
    "BR": (-34.0, -74.0, 5.0, -34.0),        # Brazil
    "MX": (14.0, -118.0, 33.0, -86.0),       # Mexico
    "AR": (-56.0, -74.0, -21.0, -53.0),      # Argentina
    "CO": (-4.0, -79.0, 13.0, -66.0),        # Colombia
    # Asia
    "IN": (6.0, 68.0, 37.0, 97.0),           # India
    "CN": (18.0, 73.0, 54.0, 135.0),         # China
    "JP": (30.0, 129.0, 46.0, 146.0),        # Japan
    "KR": (33.0, 124.0, 39.0, 131.0),        # South Korea
    "SG": (1.0, 103.5, 1.5, 104.5),          # Singapore
    "AE": (22.5, 51.5, 26.5, 56.5),          # UAE
    "SA": (16.0, 35.0, 32.0, 56.0),          # Saudi Arabia
    "IL": (29.0, 34.0, 34.0, 36.0),          # Israel
    # Oceania
    "AU": (-44.0, 113.0, -10.0, 154.0),      # Australia
    "NZ": (-47.0, 166.0, -34.0, 179.0),      # New Zealand
}


def _get_bbox_for_location(location: dict) -> Optional[str]:
    """
    Get a bounding box string from location dict.
    Uses COUNTRY_BOUNDING_BOXES for country-level filtering.
    """
    country = location.get("country", "").upper()
    bbox = COUNTRY_BOUNDING_BOXES.get(country)
    if bbox:
        return f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}"
    return None


def _build_overpass_query(
    query: str,
    location: Optional[str] = None,
    location_dict: Optional[dict] = None,
) -> str:
    """Build an Overpass QL query from a user search query."""
    # Try to map the query to OSM tags
    query_lower = query.lower().strip()
    osm_tags = None

    for keyword, tags in OSM_TAG_MAP.items():
        if keyword in query_lower:
            osm_tags = tags
            break

    if not osm_tags:
        # Fallback: search by name
        osm_tags = [f'name~"{quote_plus(query)}"']

    # Build area filter — prefer location_dict over string location
    area_filter = ""
    bbox_filter = ""

    if location_dict:
        country = location_dict.get("country", "")
        region = location_dict.get("region", "")

        # Try region-level area filter first (most specific)
        if region:
            area_filter = f'area["name"="{region}"]->.searchArea;'
        elif country:
            # Use country name for area filter
            country_names = {
                "NG": "Nigeria", "ZA": "South Africa", "KE": "Kenya",
                "GH": "Ghana", "EG": "Egypt", "GB": "United Kingdom",
                "US": "United States of America", "CA": "Canada",
                "DE": "Deutschland", "FR": "France", "IN": "India",
                "AU": "Australia", "BR": "Brasil", "AE": "United Arab Emirates",
            }
            country_name = country_names.get(country.upper(), country)
            area_filter = f'area["name"="{country_name}"]->.searchArea;'

        # Fallback: use bounding box if available
        if not area_filter:
            bbox = _get_bbox_for_location(location_dict)
            if bbox:
                bbox_filter = f"[bbox:{bbox}]"

    elif location:
        area_filter = f'area["name"="{location}"]->.searchArea;'

    # Build tag filters
    tag_filters = []
    for tag in osm_tags:
        if "=" in tag:
            key, val = tag.split("=", 1)
            tag_filters.append(f'node["{key}"="{val}"]')
            tag_filters.append(f'way["{key}"="{val}"]')
        elif "~" in tag:
            tag_filters.append(f'node[{tag}]')
            tag_filters.append(f'way[{tag}]')

    # Build the final query
    bbox_decl = f'[out:json][timeout:30]{f"[bbox:{_get_bbox_for_location(location_dict)}]" if not area_filter and location_dict and _get_bbox_for_location(location_dict) else ""};'

    if area_filter:
        elements = ";\n".join([f'{tf}(area.searchArea)' for tf in tag_filters])
        query_str = f"""
[out:json][timeout:30];
{area_filter}
(
{elements};
);
out center body;
"""
    else:
        elements = ";\n".join([f'{tf}' for tf in tag_filters])

        if bbox_filter:
            query_str = f"""
[out:json][timeout:30]{bbox_filter};
(
{elements};
);
out center body;
"""
        else:
            query_str = f"""
[out:json][timeout:30];
(
{elements};
);
out center body;
"""

    return query_str


async def search_businesses(
    query: str,
    location: Optional[str] = None,
    limit: int = 200,
    location_dict: Optional[dict] = None,
) -> List[Dict[str, Any]]:
    """
    Search for businesses using the Overpass API.

    Args:
        query: Search term (e.g., "lawyer", "dentist")
        location: String location name (legacy support)
        limit: Max results to return
        location_dict: Dict with continent/country/region keys for better filtering

    Returns list of business dicts with:
    - company_name, address, phone, website, email, latitude, longitude, category
    """
    # Parse location from query if not provided separately
    search_location = location
    search_query = query

    if not search_location and not location_dict:
        # Try to extract location: "lawyers in London" → query="lawyer", location="London"
        for sep in [" in ", " near ", " at ", " around "]:
            if sep in query.lower():
                parts = query.lower().split(sep, 1)
                search_query = parts[0].strip()
                search_location = parts[1].strip().title()
                break

    overpass_query = _build_overpass_query(search_query, search_location, location_dict)

    location_desc = location_dict.get("country", "") if location_dict else search_location
    print(f"[OVERPASS] Searching for '{search_query}' in '{location_desc}'")

    try:
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(
                OVERPASS_URL,
                data={"data": overpass_query},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

            if response.status_code != 200:
                print(f"[OVERPASS] API error: {response.status_code}")
                return []

            data = response.json()
            elements = data.get("elements", [])

            businesses = []
            for element in elements[:limit]:
                tags = element.get("tags", {})

                name = tags.get("name", "")
                if not name:
                    continue

                # Extract address components
                street = tags.get("addr:street", "")
                housenumber = tags.get("addr:housenumber", "")
                city = tags.get("addr:city", search_location or "")
                postcode = tags.get("addr:postcode", "")

                address_parts = [p for p in [housenumber, street, city, postcode] if p]
                address = ", ".join(address_parts) if address_parts else "ABSENT"

                # Get coordinates
                lat = element.get("lat") or element.get("center", {}).get("lat")
                lon = element.get("lon") or element.get("center", {}).get("lon")

                business = {
                    "company_name": name,
                    "address": address,
                    "city": city,
                    "postcode": postcode,
                    "phone": tags.get("phone", tags.get("contact:phone", "ABSENT")),
                    "website": tags.get("website", tags.get("contact:website", "ABSENT")),
                    "email": tags.get("email", tags.get("contact:email", "ABSENT")),
                    "latitude": lat,
                    "longitude": lon,
                    "category": tags.get("office", tags.get("amenity", tags.get("shop", "ABSENT"))),
                    "discovery_source": "overpass",
                }

                businesses.append(business)

            print(f"[OVERPASS] Found {len(businesses)} businesses")
            return businesses

    except httpx.TimeoutException:
        print("[OVERPASS] Request timed out")
        return []
    except Exception as e:
        print(f"[OVERPASS] Error: {e}")
        return []


async def search_businesses_without_website(
    query: str,
    location: Optional[str] = None,
    limit: int = 200,
    location_dict: Optional[dict] = None,
) -> List[Dict[str, Any]]:
    """
    Search for businesses that do NOT have a website tag.
    Used by the web_absent engine.

    Args:
        query: Search term
        location: String location name (legacy support)
        limit: Max results
        location_dict: Dict with continent/country/region keys
    """
    search_location = location
    search_query = query

    if not search_location and not location_dict:
        for sep in [" in ", " near ", " at ", " around "]:
            if sep in query.lower():
                parts = query.lower().split(sep, 1)
                search_query = parts[0].strip()
                search_location = parts[1].strip().title()
                break

    # Build query specifically filtering for NO website
    query_lower = search_query.lower().strip()
    osm_tags = None
    for keyword, tags in OSM_TAG_MAP.items():
        if keyword in query_lower:
            osm_tags = tags
            break

    if not osm_tags:
        osm_tags = [f'name~"{quote_plus(search_query)}"']

    # Area filter — prefer location_dict
    area_filter = ""
    bbox_filter = ""

    if location_dict:
        country = location_dict.get("country", "")
        region = location_dict.get("region", "")

        if region:
            area_filter = f'area["name"="{region}"]->.searchArea;'
        elif country:
            country_names = {
                "NG": "Nigeria", "ZA": "South Africa", "KE": "Kenya",
                "GH": "Ghana", "EG": "Egypt", "GB": "United Kingdom",
                "US": "United States of America", "CA": "Canada",
                "DE": "Deutschland", "FR": "France", "IN": "India",
                "AU": "Australia", "BR": "Brasil", "AE": "United Arab Emirates",
            }
            country_name = country_names.get(country.upper(), country)
            area_filter = f'area["name"="{country_name}"]->.searchArea;'

        if not area_filter:
            bbox = _get_bbox_for_location(location_dict)
            if bbox:
                bbox_filter = f"[bbox:{bbox}]"

    elif search_location:
        area_filter = f'area["name"="{search_location}"]->.searchArea;'

    tag_filters = []
    for tag in osm_tags:
        if "=" in tag:
            key, val = tag.split("=", 1)
            tf_no_website = f'node["{key}"="{val}"]["!website"]'
            tf_no_website_way = f'way["{key}"="{val}"]["!website"]'
            tag_filters.append(tf_no_website)
            tag_filters.append(tf_no_website_way)

    if area_filter:
        elements = ";\n".join([f'{tf}(area.searchArea)' for tf in tag_filters])
    else:
        elements = ";\n".join([f'{tf}' for tf in tag_filters])

    if bbox_filter:
        overpass_query = f"""
[out:json][timeout:30]{bbox_filter};
{area_filter}
(
{elements};
);
out center body;
"""
    else:
        overpass_query = f"""
[out:json][timeout:30];
{area_filter}
(
{elements};
);
out center body;
"""

    location_desc = location_dict.get("country", "") if location_dict else search_location
    print(f"[OVERPASS] Searching for businesses WITHOUT website: '{search_query}' in '{location_desc}'")

    try:
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(
                OVERPASS_URL,
                data={"data": overpass_query},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

            if response.status_code != 200:
                return []

            data = response.json()
            elements = data.get("elements", [])

            businesses = []
            for element in elements[:limit]:
                tags = element.get("tags", {})
                name = tags.get("name", "")
                if not name:
                    continue

                street = tags.get("addr:street", "")
                housenumber = tags.get("addr:housenumber", "")
                city = tags.get("addr:city", search_location or "")
                postcode = tags.get("addr:postcode", "")
                address_parts = [p for p in [housenumber, street, city, postcode] if p]
                address = ", ".join(address_parts) if address_parts else "ABSENT"

                lat = element.get("lat") or element.get("center", {}).get("lat")
                lon = element.get("lon") or element.get("center", {}).get("lon")

                business = {
                    "company_name": name,
                    "address": address,
                    "city": city,
                    "postcode": postcode,
                    "phone": tags.get("phone", tags.get("contact:phone", "ABSENT")),
                    "website": "NONE",
                    "email": tags.get("email", tags.get("contact:email", "ABSENT")),
                    "latitude": lat,
                    "longitude": lon,
                    "category": tags.get("office", tags.get("amenity", tags.get("shop", "ABSENT"))),
                    "discovery_source": "overpass",
                }
                businesses.append(business)

            print(f"[OVERPASS] Found {len(businesses)} businesses without website")
            return businesses

    except Exception as e:
        print(f"[OVERPASS] Error: {e}")
        return []
