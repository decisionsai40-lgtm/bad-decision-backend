"""
BAD DECISION AI — Gate 1: DNS Resolution Check
================================================
Fastest check. Verifies the website's domain resolves.
All tiers get this gate.
"""

import dns.resolver
from urllib.parse import urlparse


async def check_dns(website_url: str) -> bool:
    """Check if a website's domain has a valid DNS record."""
    try:
        if not website_url or website_url == "ABSENT":
            return False

        parsed = urlparse(website_url)
        domain = parsed.hostname or parsed.path.split("/")[0]

        if not domain:
            return False

        resolver = dns.resolver.Resolver()
        resolver.timeout = 5
        resolver.lifetime = 5

        answers = resolver.resolve(domain, "A")
        return len(answers) > 0

    except dns.resolver.NXDOMAIN:
        print(f"[GATE1-DNS] {website_url} — NXDOMAIN")
        return False
    except dns.resolver.NoAnswer:
        print(f"[GATE1-DNS] {website_url} — No A record")
        return False
    except dns.resolver.Timeout:
        print(f"[GATE1-DNS] {website_url} — Timeout")
        return False
    except Exception as e:
        print(f"[GATE1-DNS] {website_url} — Error: {e}")
        return False
