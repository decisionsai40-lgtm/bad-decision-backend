"""
BAD DECISION — SEC EDGAR API Wrapper
======================================
Finds real decision-maker names for US public companies.
Completely free, no API key required.

Usage:
  officers = await get_company_officers("Apple Inc")
  # officers = [{"name": "Tim Cook", "role": "CEO"}, ...]
"""

import httpx
from typing import List, Dict, Any

from config import SEC_EDGAR_BASE_URL, SEC_EDGAR_USER_AGENT


async def get_company_officers(company_name: str) -> List[Dict[str, Any]]:
    """
    Look up a US company's officers/directors via SEC EDGAR.

    Args:
        company_name: The company name to search for

    Returns:
        List of {"name": "...", "role": "..."} dicts, or empty list if not found
    """
    if not company_name:
        return []

    try:
        # Step 1: Search for the company CIK number
        async with httpx.AsyncClient(timeout=15) as client:
            search_res = await client.get(
                "https://efts.sec.gov/LATEST/search-index",
                params={"q": f'"{company_name}"', "forms": "10-K"},
                headers={"User-Agent": SEC_EDGAR_USER_AGENT},
            )

            if search_res.status_code != 200:
                return []

            search_data = search_res.json()
            hits = search_data.get("hits", {}).get("hits", [])

            if not hits:
                return []

            # Get the CIK from the first hit
            cik = hits[0].get("_source", {}).get("entity", "")
            if not cik:
                return []

            # Step 2: Get company submissions (includes officers)
            submissions_res = await client.get(
                f"{SEC_EDGAR_BASE_URL}/submissions/CIK{cik.zfill(10)}.json",
                headers={"User-Agent": SEC_EDGAR_USER_AGENT},
            )

            if submissions_res.status_code != 200:
                return []

            submissions = submissions_res.json()

            # The officers are in the 10-K filings
            officers = []
            for filing in submissions.get("filings", {}).get("recent", {}).get("form", []):
                if filing == "10-K":
                    # Found a 10-K — extract officers from it
                    # SEC returns a simplified list of officers
                    for officer in submissions.get("entities", [{}])[0].get("officers", []):
                        officers.append({
                            "name": officer.get("name", ""),
                            "role": officer.get("title", ""),
                        })
                    break

            return officers[:5]  # Limit to top 5 officers

    except Exception as e:
        print(f"[SEC_EDGAR] Error for {company_name}: {e}")
        return []
