"""
BAD DECISION — ScrapingAnt Cloud Scraping API Wrapper
======================================================
ScrapingAnt renders JavaScript in the cloud and returns the full HTML.
Used for sites that require JS (Yelp, Reddit, Meta Ads Library, Google Maps).

Free tier: 10,000 API credits/month (recurring, no credit card).

Usage:
  html = await scrape_with_js("https://www.yelp.com/search?find_desc=cafe&find_loc=NYC")
"""

import httpx
from typing import Optional, Dict, Any

from config import SCRAPINGANT_API_KEY, SCRAPINGANT_BASE_URL, SCRAPINGANT_TIMEOUT


async def scrape_with_js(
    url: str,
    wait_for: str = "",
    return_text_only: bool = False,
) -> Optional[str]:
    """
    Scrape a URL using ScrapingAnt (renders JavaScript in the cloud).

    Args:
        url: The URL to scrape
        wait_for: CSS selector to wait for before returning (optional)
        return_text_only: If True, return plain text instead of HTML

    Returns:
        HTML string (or text if return_text_only), or None if failed
    """
    if not SCRAPINGANT_API_KEY:
        print("[SCRAPINGANT] No API key configured — skipping JS scrape")
        return None

    # SSRF protection: block internal/private IPs
    if _is_internal_url(url):
        print(f"[SCRAPINGANT] Blocked internal URL: {url}")
        return None

    params: Dict[str, Any] = {
        "url": url,
        "x-api-key": SCRAPINGANT_API_KEY,
    }

    if wait_for:
        params["wait_for"] = wait_for

    if return_text_only:
        params["return_text_only"] = "true"

    try:
        async with httpx.AsyncClient(timeout=SCRAPINGANT_TIMEOUT) as client:
            response = await client.get(
                SCRAPINGANT_BASE_URL + "/general",
                params=params,
            )

            if response.status_code == 429:
                print("[SCRAPINGANT] Rate limit hit (429) — too many requests")
                return None

            if response.status_code == 403:
                print("[SCRAPINGANT] Invalid API key (403)")
                return None

            if response.status_code != 200:
                print(f"[SCRAPINGANT] Error {response.status_code}: {response.text[:200]}")
                return None

            data = response.json()
            content = data.get("content", "")

            if content:
                print(f"[SCRAPINGANT] Scraped {url[:60]}... ({len(content)} chars)")
                return content

            print(f"[SCRAPINGANT] Empty response for {url[:60]}...")
            return None

    except httpx.TimeoutException:
        print(f"[SCRAPINGANT] Timeout for {url[:60]}...")
        return None

    except Exception as e:
        print(f"[SCRAPINGANT] Error: {e}")
        return None


def _is_internal_url(url: str) -> bool:
    """SSRF protection — block requests to internal/private IP ranges."""
    if not url:
        return True

    url_lower = url.lower()

    # Block obvious internal targets
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
