"""
BAD DECISION — Domain Age Lookup
================================
Looks up the registration date of a domain using free RDAP (Registration
Data Access Protocol) — the modern replacement for WHOIS.

Returns the domain age in days, which is used by the ecommerce engine
to show "Store Age" on lead cards.
"""
import httpx
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse


async def get_domain_age_days(url: str) -> Optional[int]:
    """
    Get the age of a domain in days since registration.

    Uses RDAP (https://rdap.org) — a free, public, no-auth-required service.

    Args:
        url: Website URL (e.g., "https://example.com")

    Returns:
        Number of days since domain registration, or None if lookup fails.
    """
    if not url or url == "ABSENT":
        return None

    try:
        # Extract domain from URL
        if not url.startswith("http"):
            url = "https://" + url
        parsed = urlparse(url)
        domain = (parsed.hostname or "").lower()
        if domain.startswith("www."):
            domain = domain[4:]
        if not domain:
            return None

        # Use RDAP to look up registration date
        # rdap.org redirects to the authoritative RDAP server for the TLD
        async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
            response = await client.get(f"https://rdap.org/domain/{domain}")
            if response.status_code != 200:
                return None

            data = response.json()

            # RDAP returns events array with registration date
            events = data.get("events", [])
            for event in events:
                if event.get("eventAction") == "registration":
                    reg_date_str = event.get("eventDate", "")
                    if reg_date_str:
                        # Parse ISO 8601 date
                        reg_date = datetime.fromisoformat(
                            reg_date_str.replace("Z", "+00:00")
                        )
                        now = datetime.now(timezone.utc)
                        age_days = (now - reg_date).days
                        if age_days > 0:
                            return age_days

        return None

    except Exception as e:
        print(f"[DOMAIN_AGE] Error looking up {url}: {e}")
        return None
