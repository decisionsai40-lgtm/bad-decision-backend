"""
BAD DECISION — Gate 1: DNS Footprint Check
============================================
This is the FASTEST check. We verify that the domain exists AND has
MX records (mail server configured). If the domain is dead or has no
mail server, we drop the lead immediately — no point checking further.

Speed: Very fast (< 1 second)
Who gets it: ALL tiers (Free, Starter, Growth, Pro)
"""

import dns.resolver
from urllib.parse import urlparse
from typing import Tuple


async def check_dns(website_url: str) -> Tuple[bool, bool]:
    """
    Check if a website's domain has valid DNS records AND MX records.

    Args:
        website_url: The website to check (e.g., "https://abcroofing.com")

    Returns:
        (domain_exists, has_mx)
        - domain_exists: True = domain resolves, False = dead domain
        - has_mx: True = mail server configured, False = no MX record
    """
    try:
        if not website_url or website_url == "ABSENT":
            return False, False

        parsed = urlparse(website_url)
        domain = parsed.hostname or parsed.path.split("/")[0]

        if not domain:
            return False, False

        resolver = dns.resolver.Resolver()
        resolver.timeout = 5
        resolver.lifetime = 5

        # Check A record (domain exists)
        domain_exists = False
        try:
            answers = resolver.resolve(domain, "A")
            domain_exists = len(answers) > 0
        except dns.resolver.NXDOMAIN:
            print(f"[GATE1-DNS] {website_url} — NXDOMAIN (domain does not exist)")
            return False, False
        except dns.resolver.NoAnswer:
            # Domain might exist but no A record — try CNAME
            try:
                answers = resolver.resolve(domain, "CNAME")
                domain_exists = len(answers) > 0
            except Exception:
                pass
        except dns.resolver.Timeout:
            print(f"[GATE1-DNS] {website_url} — Timeout")
            return False, False

        if not domain_exists:
            return False, False

        # Check MX record (mail server configured)
        has_mx = False
        try:
            mx_records = resolver.resolve(domain, "MX")
            has_mx = len(mx_records) > 0
        except dns.resolver.NoAnswer:
            # No MX record — domain exists but can't receive email
            has_mx = False
        except dns.resolver.NXDOMAIN:
            has_mx = False
        except Exception:
            has_mx = False

        if not has_mx:
            print(f"[GATE1-DNS] {domain} — No MX record (cannot receive email)")

        return domain_exists, has_mx

    except Exception as e:
        print(f"[GATE1-DNS] {website_url} — Error: {e}")
        return False, False


async def check_dns_simple(website_url: str) -> bool:
    """
    Simple boolean DNS check (domain exists only, no MX check).
    Used by engines that just need to know if a website is live.
    """
    domain_exists, _ = await check_dns(website_url)
    return domain_exists
