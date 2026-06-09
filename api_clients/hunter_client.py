"""
BAD DECISION AI — Hunter.io Email Finder Client
================================================
25 free searches/month. Finds verified business emails.
"""

import httpx
from typing import Tuple
from config import HUNTER_API_KEY


async def find_email(domain: str, company_name: str = "") -> Tuple[str, str]:
    """
    Find a business email using Hunter.io domain search.
    Returns (email, email_source) or ("", "") if not found.
    """
    if not HUNTER_API_KEY:
        return "", ""

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                "https://api.hunter.io/v2/email-finder",
                params={
                    "domain": domain,
                    "company": company_name,
                    "api_key": HUNTER_API_KEY,
                },
            )

            if response.status_code == 200:
                data = response.json().get("data", {})
                email = data.get("email", "")
                if email:
                    return email, "hunter_io"

            return "", ""

    except Exception as e:
        print(f"[HUNTER] Error: {e}")
        return "", ""


async def find_email_by_name(company_name: str, location: str = "") -> Tuple[str, str]:
    """
    Find an email using company name search (for businesses without domains).
    """
    if not HUNTER_API_KEY:
        return "", ""

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                "https://api.hunter.io/v2/domain-search",
                params={
                    "company": company_name,
                    "api_key": HUNTER_API_KEY,
                },
            )

            if response.status_code == 200:
                data = response.json().get("data", {})
                emails = data.get("emails", [])
                if emails:
                    return emails[0].get("value", ""), "hunter_io"

            return "", ""

    except Exception as e:
        print(f"[HUNTER] Name search error: {e}")
        return "", ""
