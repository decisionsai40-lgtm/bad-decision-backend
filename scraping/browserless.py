"""
BAD DECISION — Browser Based Page Reader
=========================================
Replaces ScrapingAnt. Uses Browserless.io for JS-rendered page scraping.
Free tier: 2 concurrent browsers. Scale tier: $350/mo for 100K+ renders.
Includes 7-day cache layer (when Redis is available in Phase D).
"""
import httpx
import os
from typing import Optional

BROWSERLESS_API_KEY = os.getenv("BROWSERLESS_API_KEY", "").strip()
BROWSERLESS_BASE_URL = os.getenv("BROWSERLESS_BASE_URL", "https://chrome.browserless.io").strip()
SOURCE_TIMEOUT = 30


async def scrape_with_js(url: str, wait_for: str = "") -> Optional[str]:
    """
    Scrape a URL using Browserless.io (renders JavaScript in the cloud).
    Returns HTML string, or None if failed.

    Replaces the old ScrapingAnt integration. Same interface so engines
    don't need to change their import patterns much.
    """
    if not BROWSERLESS_API_KEY:
        print(f"[BROWSERLESS] No API key configured, skipping JS scrape for {url[:60]}")
        return None

    # SSRF protection — block internal/private IPs
    if _is_internal_url(url):
        print(f"[BROWSERLESS] Blocked internal URL: {url}")
        return None

    params = {
        "token": BROWSERLESS_API_KEY,
        "url": url,
    }
    if wait_for:
        params["waitFor"] = wait_for

    try:
        async with httpx.AsyncClient(timeout=SOURCE_TIMEOUT) as client:
            response = await client.get(
                f"{BROWSERLESS_BASE_URL}/content",
                params=params,
            )

            if response.status_code == 429:
                print("[BROWSERLESS] Rate limit hit (429)")
                return None
            if response.status_code == 403:
                print("[BROWSERLESS] Invalid API key (403)")
                return None
            if response.status_code != 200:
                print(f"[BROWSERLESS] Error {response.status_code}: {response.text[:200]}")
                return None

            content = response.text
            if content:
                print(f"[BROWSERLESS] Scraped {url[:60]}... ({len(content)} chars)")
                return content

            print(f"[BROWSERLESS] Empty response for {url[:60]}...")
            return None

    except httpx.TimeoutException:
        print(f"[BROWSERLESS] Timeout for {url[:60]}...")
        return None
    except Exception as e:
        print(f"[BROWSERLESS] Error: {e}")
        return None


def _is_internal_url(url: str) -> bool:
    """SSRF protection — block requests to internal/private IP ranges."""
    if not url:
        return True
    url_lower = url.lower()
    blocked = [
        "localhost", "127.0.0.1", "0.0.0.0", "::1",
        "10.", "172.16.", "172.17.", "172.18.", "172.19.",
        "172.20.", "172.21.", "172.22.", "172.23.", "172.24.",
        "172.25.", "172.26.", "172.27.", "172.28.", "172.29.",
        "172.30.", "172.31.", "192.168.", "169.254.",
        ".internal", ".local", "metadata.google",
    ]
    for b in blocked:
        if b in url_lower:
            return True
    return False
