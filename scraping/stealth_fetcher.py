"""
BAD DECISION — Stealth Web Fetcher (Scrapling)
===============================================
Uses Scrapling's Fetcher mode (curl_cffi backend) to fetch web pages
with Chrome TLS fingerprint impersonation.

WHAT THIS BYPASSES:
  - TLS fingerprinting (JA3/JA4) — looks like real Chrome
  - Basic header-based bot detection — auto-generates real browser headers
  - HTTP/2 fingerprinting — mimics browser connection patterns

WHAT THIS DOES NOT BYPASS (would need StealthyFetcher + browser):
  - Cloudflare JS challenges / Turnstile CAPTCHA
  - DataDome / PerimeterX / Kasada advanced protections
  - Sites requiring JavaScript execution

NO browser or Playwright required — works on Render free tier.

NOTE: This module is now used ONLY for static HTML pages (Meta Ads Library,
Yelp, Houzz, Reddit, GitHub, OpenCorporates, company websites). All Google
search queries go through Serper.dev (see scraping/serper_search.py) and all
local business queries go through OpenStreetMap (see scraping/osm_search.py).
"""

from scrapling import Fetcher
from typing import Optional, Dict, Any, List
from urllib.parse import quote_plus, urlencode, urljoin
import re

# Create a reusable Fetcher instance
_fetcher = Fetcher()


async def stealth_fetch(
    url: str,
    timeout: int = 15,
) -> Optional[Dict[str, Any]]:
    """
    Fetch a webpage using Scrapling's Fetcher (curl_cffi based).
    Impersonates Chrome's TLS fingerprint and sends stealthy headers.

    Args:
        url: The webpage to fetch
        timeout: How many seconds to wait before giving up (default 15)

    Returns:
        Dictionary with page content and status, or None if failed
    """
    try:
        response = _fetcher.get(
            url,
            impersonate="chrome",
            timeout=timeout,
        )
        if response and response.status == 200:
            return {
                "status": 200,
                "html": response.text,
                "url": str(response.url),
            }
        else:
            status = response.status if response else "No response"
            print(f"[STEALTH] HTTP {status} for {url}")
    except Exception as e:
        print(f"[STEALTH] Fetch error for {url}: {e}")

    return None


def extract_text_from_html(html: str, max_chars: int = 15000) -> str:
    """
    Strip HTML tags and return clean text, truncated to max_chars.
    This is passed to DeepSeek for structuring — we don't want to
    send raw HTML because it wastes tokens.
    """
    if not html:
        return ""

    # Remove script and style blocks entirely
    text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)

    # Remove HTML tags
    text = re.sub(r'<[^>]+>', ' ', text)

    # Clean up whitespace
    text = re.sub(r'\s+', ' ', text).strip()

    # Decode common HTML entities
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&quot;', '"').replace('&#39;', "'")
    text = text.replace('&nbsp;', ' ')

    return text[:max_chars]


def extract_links_from_html(html: str, base_url: str = "") -> List[str]:
    """Extract all href links from HTML."""
    if not html:
        return []
    links = re.findall(r'href=["\'](https?://[^"\']+)', html, re.IGNORECASE)
    if base_url:
        # Also grab relative links
        rel_links = re.findall(r'href=["\'](/[^"\']+)', html, re.IGNORECASE)
        links.extend([urljoin(base_url, link) for link in rel_links])
    return list(set(links))


# ============================================================
# PLATFORM-SPECIFIC URL BUILDERS
# These construct the exact URLs Scrapling will fetch.
# Google Search and Google Maps URL builders have been REMOVED —
# use Serper.dev (scraping/serper_search.py) and OpenStreetMap
# (scraping/osm_search.py) instead.
# ============================================================

def build_meta_ads_library_url(query: str) -> str:
    """Build a Meta Ads Library search URL."""
    q = quote_plus(query)
    return f"https://www.facebook.com/ads/library/?active_status=all&ad_type=all&country=US&q={q}"


def build_yelp_search_url(query: str, location: str = "") -> str:
    """Build a Yelp search URL."""
    params = {"find_desc": query}
    if location:
        params["find_loc"] = location
    return f"https://www.yelp.com/search?{urlencode(params)}"


def build_houzz_search_url(query: str) -> str:
    """Build a Houzz search URL."""
    q = quote_plus(query)
    return f"https://www.houzz.com/professionals/searchQuery?q={q}"


def build_github_search_url(query: str) -> str:
    """Build a GitHub Discussions/Issues search URL."""
    q = quote_plus(query)
    return f"https://github.com/search?q={q}&type=issues"


def build_reddit_search_url(query: str) -> str:
    """Build a Reddit search URL for recent posts."""
    q = quote_plus(query)
    return f"https://www.reddit.com/search/?q={q}&sort=new&t=hour"


def build_opencorporates_url(query: str) -> str:
    """Build an OpenCorporates search URL."""
    q = quote_plus(query)
    return f"https://opencorporates.com/companies?q={q}"
