"""
BAD DECISION AI — Gate 1: DNS Resolution Check
================================================
This is the FASTEST check. We try to look up the website's
DNS record. If the website doesn't exist (no DNS record),
we drop the lead immediately — no point checking further.

Speed: Very fast (< 1 second)
Who gets it: ALL tiers (Free, Starter, Growth, Pro)
"""

import dns.resolver
from urllib.parse import urlparse


async def check_dns(website_url: str) -> bool:
    """
    Check if a website's domain has a valid DNS record.

    Think of DNS like a phone book. We're checking:
    "Does this website's name exist in the phone book?"
    If not → the website is dead → DROP this lead.

    Args:
        website_url: The website to check (e.g., "https://abcroofing.com")

    Returns:
        True = website exists, False = website is dead
    """
    try:
        # Extract just the domain name from the full URL
        # "https://abcroofing.com/about" → "abcroofing.com"
        if not website_url or website_url == "ABSENT":
            return False

        parsed = urlparse(website_url)
        domain = parsed.hostname or parsed.path.split("/")[0]

        if not domain:
            return False

        # Look up the DNS "A" record (the IP address)
        # This is like checking if the name is in the phone book
        resolver = dns.resolver.Resolver()
        resolver.timeout = 5  # Wait max 5 seconds
        resolver.lifetime = 5

        answers = resolver.resolve(domain, "A")

        # If we got here, DNS resolved successfully
        return len(answers) > 0

    except dns.resolver.NXDOMAIN:
        # Domain doesn't exist at all
        print(f"[GATE1-DNS] {website_url} — NXDOMAIN (domain does not exist)")
        return False

    except dns.resolver.NoAnswer:
        # Domain exists but no A record
        print(f"[GATE1-DNS] {website_url} — No A record")
        return False

    except dns.resolver.Timeout:
        # DNS lookup took too long — treat as failed
        print(f"[GATE1-DNS] {website_url} — Timeout")
        return False

    except Exception as e:
        # Something else went wrong — be cautious and fail
        print(f"[GATE1-DNS] {website_url} — Error: {e}")
        return False
