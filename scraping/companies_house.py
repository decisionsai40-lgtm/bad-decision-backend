"""
BAD DECISION — Companies House API Wrapper (UK)
=================================================
Finds real director names for UK companies.
Free API, requires registration for an API key.

Usage:
  officers = await get_uk_company_officers("Bad Decision Ltd")
  # officers = [{"name": "John Smith", "role": "Director"}, ...]
"""

import httpx
import base64
from typing import List, Dict, Any

from config import COMPANIES_HOUSE_API_KEY, COMPANIES_HOUSE_BASE_URL


async def get_uk_company_officers(company_name: str) -> List[Dict[str, Any]]:
    """
    Look up a UK company's directors/officers via Companies House.

    Args:
        company_name: The company name to search for

    Returns:
        List of {"name": "...", "role": "..."} dicts, or empty list if not found
    """
    if not company_name or not COMPANIES_HOUSE_API_KEY:
        return []

    try:
        # Basic auth: API key as username, empty password
        auth = base64.b64encode(f"{COMPANIES_HOUSE_API_KEY}:".encode()).decode()

        async with httpx.AsyncClient(timeout=15) as client:
            # Step 1: Search for the company
            search_res = await client.get(
                f"{COMPANIES_HOUSE_BASE_URL}/search/companies",
                params={"q": company_name, "items_per_page": 1},
                headers={
                    "Authorization": f"Basic {auth}",
                    "Accept": "application/json",
                },
            )

            if search_res.status_code != 200:
                return []

            search_data = search_res.json()
            items = search_data.get("items", [])

            if not items:
                return []

            # Get the company number
            company_number = items[0].get("company_number", "")
            if not company_number:
                return []

            # Step 2: Get the officers
            officers_res = await client.get(
                f"{COMPANIES_HOUSE_BASE_URL}/company/{company_number}/officers",
                headers={
                    "Authorization": f"Basic {auth}",
                    "Accept": "application/json",
                },
            )

            if officers_res.status_code != 200:
                return []

            officers_data = officers_res.json()
            officers = []

            for item in officers_data.get("items", [])[:5]:  # Top 5 officers
                officers.append({
                    "name": item.get("name", "").replace(",", "").strip(),
                    "role": item.get("officer_role", "Director").replace("_", " ").title(),
                })

            return officers

    except Exception as e:
        print(f"[COMPANIES_HOUSE] Error for {company_name}: {e}")
        return []
