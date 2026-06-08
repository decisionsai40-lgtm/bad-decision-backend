"""
BAD DECISION AI — Stealth Web Fetcher (Scrapling-First)
========================================================
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
"""

from scrapling import Fetcher
from typing import Optional, Dict, Any, List
from urllib.parse import quote_plus, urlencode, urlparse, urljoin
import re
import json

# Create a reusable Fetcher instance
_fetcher = Fetcher()


async def stealth_fetch(
    url: str,
    timeout: int = 30,
) -> Optional[Dict[str, Any]]:
    """
    Fetch a webpage using Scrapling's Fetcher (curl_cffi based).
    Impersonates Chrome's TLS fingerprint and sends stealthy headers.

    Args:
        url: The webpage to fetch
        timeout: How many seconds to wait before giving up

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


def extract_with_adaptive_css(page, selector: str) -> list:
    """
    Use Scrapling's adaptive CSS extraction to find elements
    even when the website structure changes.
    """
    if page is None:
        return []
    try:
        elements = page.css(selector)
        return elements
    except Exception as e:
        print(f"[STEALTH] CSS extraction error: {e}")
        return []


def extract_text_from_html(html: str, max_chars: int = 15000, min_useful_length: int = 50) -> str:
    """
    Strip HTML tags and return clean text, truncated to max_chars.
    This is passed to DeepSeek for structuring — we don't want to
    send raw HTML because it wastes tokens.

    If the extracted text is very short (under min_useful_length),
    it likely means the page is JS-rendered and the content isn't
    in the raw HTML. Returns empty string in that case.
    """
    if not html:
        return ""

    # Remove script and style blocks entirely
    text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<noscript[^>]*>.*?</noscript>', '', text, flags=re.DOTALL | re.IGNORECASE)

    # Remove HTML tags
    text = re.sub(r'<[^>]+>', ' ', text)

    # Clean up whitespace
    text = re.sub(r'\s+', ' ', text).strip()

    # Decode common HTML entities
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&quot;', '"').replace('&#39;', "'")
    text = text.replace('&nbsp;', ' ')

    # If the extracted text is very short, the page is likely JS-rendered
    # and we won't get useful data from it
    if len(text) < min_useful_length:
        return ""

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
# These construct the exact URLs Scrapling will fetch
# ============================================================

def build_google_search_url(query: str, num_results: int = 25) -> str:
    """Build a Google search URL for finding businesses."""
    q = quote_plus(f"{query} business")
    return f"https://www.google.com/search?q={q}&num={num_results}&hl=en"


def build_google_maps_url(query: str, location: str = "") -> str:
    """Build a Google Maps search URL for finding local businesses."""
    search_term = f"{query} {location}".strip() if location else query
    q = quote_plus(search_term)
    return f"https://www.google.com/maps/search/{q}"


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


def build_linkedin_search_url(query: str) -> str:
    """Build a LinkedIn public search URL."""
    q = quote_plus(query)
    return f"https://www.linkedin.com/search/results/content/?keywords={q}"


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


def build_bing_search_url(query: str, num_results: int = 25) -> str:
    """Build a Bing search URL — often returns more text content than Google."""
    q = quote_plus(f"{query} business")
    return f"https://www.bing.com/search?q={q}&count={num_results}"


def build_duckduckgo_search_url(query: str) -> str:
    """Build a DuckDuckGo HTML search URL — lightweight, less JS-heavy."""
    q = quote_plus(f"{query} business")
    return f"https://html.duckduckgo.com/html/?q={q}"
