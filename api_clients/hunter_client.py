"""
BAD DECISION AI — Hunter.io API Client
=======================================
FREE: 25 domain searches + 50 verifications per month.
Returns email addresses found for a domain + email pattern.
"""

import httpx
from typing import Optional, Dict, Any, List
from config import HUNTER_API_KEY


HUNTER_DOMAIN_SEARCH_URL = "https://api.hunter.io/v2/domain-search"
HUNTER_EMAIL_VERIFY_URL = "https://api.hunter.io/v2/email-verifier"


async def hunter_domain_search(domain: str) -> Optional[Dict[str, Any]]:
    """
    Search for emails at a specific domain.
    Returns emails found + the email pattern used by the company.
    """
    if not HUNTER_API_KEY:
        return None

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                HUNTER_DOMAIN_SEARCH_URL,
                params={"domain": domain, "api_key": HUNTER_API_KEY, "limit": 10},
            )

            if response.status_code != 200:
                return None

            data = response.json().get("data", {})

            emails = []
            for email_data in data.get("emails", []):
                emails.append({
                    "email": email_data.get("value", ""),
                    "type": email_data.get("type", ""),  # personal, generic
                    "confidence": email_data.get("confidence", 0),
                    "first_name": email_data.get("first_name", ""),
                    "last_name": email_data.get("last_name", ""),
                    "position": email_data.get("position", ""),
                    "sources": [s.get("uri", "") for s in email_data.get("sources", [])[:3]],
                })

            pattern = data.get("pattern", "")

            result = {
                "emails": emails,
                "pattern": pattern,  # e.g., "{first}.{last}"
                "domain": domain,
            }

            print(f"[HUNTER] Found {len(emails)} emails for {domain} (pattern: {pattern})")
            return result

    except Exception as e:
        print(f"[HUNTER] Error for {domain}: {e}")
        return None


async def hunter_verify_email(email: str) -> Optional[Dict[str, Any]]:
    """Verify an email address using Hunter.io."""
    if not HUNTER_API_KEY:
        return None

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                HUNTER_EMAIL_VERIFY_URL,
                params={"email": email, "api_key": HUNTER_API_KEY},
            )

            if response.status_code != 200:
                return None

            data = response.json().get("data", {})
            return {
                "email": email,
                "result": data.get("result", ""),  # deliverable, undeliverable, risky, unknown
                "score": data.get("score", 0),
                "smtp_check": data.get("smtp_check", False),
                "mx_records": data.get("mx_records", False),
            }

    except Exception as e:
        print(f"[HUNTER] Verify error for {email}: {e}")
        return None
