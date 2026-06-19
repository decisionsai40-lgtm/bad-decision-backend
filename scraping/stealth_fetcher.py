"""
BAD DECISION — Stealth Web Fetcher (Scrapling + selectolax)
==============================================================
Uses Scrapling's Fetcher mode (curl_cffi backend) to fetch web pages
with Chrome TLS fingerprint impersonation. Uses selectolax for fast
HTML parsing (30x faster than BeautifulSoup).

NOTE: This module is used for static HTML pages only. For JS-heavy
sites (Yelp, Reddit, Meta Ads), use scraping/scrapingant.py instead.
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


def extract_text_from_html(html: str, max_chars: int = 15000) -> str:
    """
    Strip HTML tags and return clean text using selectolax.
    30x faster than BeautifulSoup.

    Args:
        html: Raw HTML string
        max_chars: Maximum characters to return

    Returns:
        Clean text extracted from the HTML
    """
    if not html:
        return ""

    try:
        from selectolax.parser import HTMLParser

        tree = HTMLParser(html)

        # Remove script and style tags
        for tag in tree.css("script"):
            tag.decompose()
        for tag in tree.css("style"):
            tag.decompose()
        for tag in tree.css("noscript"):
            tag.decompose()

        # Get text
        text = tree.text(separator=" ", strip=True)

        # Decode common HTML entities
        text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        text = text.replace("&quot;", '"').replace("&#39;", "'")
        text = text.replace("&nbsp;", " ")

        # Clean up whitespace
        text = re.sub(r'\s+', ' ', text).strip()

        return text[:max_chars]

    except ImportError:
        # Fallback to regex if selectolax is not installed
        text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
        text = text.replace('&quot;', '"').replace('&#39;', "'").replace('&nbsp;', ' ')
        return text[:max_chars]


def extract_links_from_html(html: str, base_url: str = "") -> List[str]:
    """Extract all href links from HTML using selectolax."""
    if not html:
        return []

    try:
        from selectolax.parser import HTMLParser

        tree = HTMLParser(html)
        links = []

        for node in tree.css("a[href]"):
            href = node.attributes.get("href", "")
            if href:
                if href.startswith("http"):
                    links.append(href)
                elif href.startswith("/") and base_url:
                    links.append(urljoin(base_url, href))

        return list(set(links))

    except ImportError:
        # Fallback to regex
        links = re.findall(r'href=["\'](https?://[^"\']+)', html, re.IGNORECASE)
        if base_url:
            rel_links = re.findall(r'href=["\'](/[^"\']+)', html, re.IGNORECASE)
            links.extend([urljoin(base_url, link) for link in rel_links])
        return list(set(links))


def extract_emails_from_html(html: str) -> List[str]:
    """Extract all email addresses from HTML using selectolax + regex."""
    if not html:
        return []

    from selectolax.parser import HTMLParser

    tree = HTMLParser(html)

    # Remove scripts (they contain tracking pixels and fake emails)
    for tag in tree.css("script"):
        tag.decompose()

    emails = set()

    # Find mailto: links
    for node in tree.css("a[href^='mailto:']"):
        href = node.attributes.get("href", "")
        if href.startswith("mailto:"):
            email = href[7:].split("?")[0].lower().strip()  # Remove ?subject= etc.
            if email and "@" in email:
                emails.add(email)

    # Find emails in text
    text = tree.text(separator=" ")
    email_pattern = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}')
    for match in email_pattern.finditer(text):
        email = match.group(0).lower().strip()
        if email and not _is_spammy_email(email):
            emails.add(email)

    return list(emails)


def _is_spammy_email(email: str) -> bool:
    """Check if an email is likely spammy or not a real business email."""
    if not email:
        return True
    if any(email.endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg']):
        return True
    if len(email) > 100:
        return True

    suspicious_domains = [
        'example.com', 'test.com', 'sentry.io', 'wixpress.com',
        'godaddy.com', 'cloudflare.com', 'google.com', 'facebook.com',
        'twitter.com', 'instagram.com', 'linkedin.com', 'youtube.com',
        'noreply.github.com', 'example.org', 'sentry-next.wixpress.com'
    ]
    domain = email.split('@')[-1] if '@' in email else ''
    if domain in suspicious_domains:
        return True

    if re.match(r'^[a-f0-9]{32}@', email):
        return True

    return False


# ============================================================
# URL BUILDERS (only for sites that work with static HTML)
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
    """Build a GitHub search URL."""
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
