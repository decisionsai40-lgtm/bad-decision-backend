"""
BAD DECISION AI — Stealth Web Fetcher (Scrapling-First)
========================================================
CRITICAL FIX: Uses response.body instead of response.text.
"""

from scrapling import Fetcher
from typing import Optional, Dict, Any, List
from urllib.parse import quote_plus, urlencode, urlparse, urljoin
import re

_fetcher = Fetcher()


async def stealth_fetch(url: str, timeout: int = 30) -> Optional[Dict[str, Any]]:
    """Fetch a webpage using Scrapling's Fetcher with Chrome impersonation."""
    try:
        response = _fetcher.get(url, impersonate="chrome", timeout=timeout)
        if response and response.status == 200:
            html_content = response.body.decode('utf-8', errors='replace') if isinstance(response.body, bytes) else str(response.body)
            return {"status": 200, "html": html_content, "url": str(response.url)}
        else:
            status = response.status if response else "No response"
            print(f"[STEALTH] HTTP {status} for {url}")
    except Exception as e:
        print(f"[STEALTH] Fetch error for {url}: {e}")
    return None


def extract_text_from_html(html: str, max_chars: int = 15000) -> str:
    if not html:
        return ""
    text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&quot;', '"').replace('&#39;', "'").replace('&nbsp;', ' ')
    return text[:max_chars]


def extract_links_from_html(html: str, base_url: str = "") -> List[str]:
    if not html:
        return []
    links = re.findall(r'href=["\'](https?://[^"\']+)', html, re.IGNORECASE)
    if base_url:
        rel_links = re.findall(r'href=["\'](/[^"\']+)', html, re.IGNORECASE)
        links.extend([urljoin(base_url, link) for link in rel_links])
    return list(set(links))


def extract_emails_from_html(html: str) -> List[str]:
    if not html:
        return []
    pattern = r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}'
    emails = re.findall(pattern, html)
    skip = {'.png', '.jpg', '.gif', '.svg', '.css', '.js', '.woff'}
    return [e for e in set(emails) if not any(e.endswith(s) for s in skip)]


def extract_phones_from_html(html: str) -> List[str]:
    if not html:
        return []
    pattern = r'(?:\+?1?\s*[-.]?\s*(?:\(\d{3}\)|\d{3})\s*[-.]?\s*\d{3}\s*[-.]?\s*\d{4})'
    return list(set(re.findall(pattern, html)))


def build_google_search_url(query: str, num_results: int = 25) -> str:
    return f"https://www.google.com/search?q={quote_plus(query)}&num={num_results}&hl=en"

def build_google_maps_url(query: str, location: str = "") -> str:
    search_term = f"{query} {location}".strip() if location else query
    return f"https://www.google.com/maps/search/{quote_plus(search_term)}"

def build_meta_ads_library_url(query: str) -> str:
    return f"https://www.facebook.com/ads/library/?active_status=all&ad_type=all&country=US&q={quote_plus(query)}"

def build_yelp_search_url(query: str, location: str = "") -> str:
    params = {"find_desc": query}
    if location:
        params["find_loc"] = location
    return f"https://www.yelp.com/search?{urlencode(params)}"

def build_houzz_search_url(query: str) -> str:
    return f"https://www.houzz.com/professionals/searchQuery?q={quote_plus(query)}"

def build_reddit_search_url(query: str) -> str:
    return f"https://www.reddit.com/search/?q={quote_plus(query)}&sort=new&t=hour"

def build_reddit_json_url(query: str) -> str:
    return f"https://www.reddit.com/search.json?q={quote_plus(query)}&sort=new&t=hour&limit=25"
