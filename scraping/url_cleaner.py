"""
BAD DECISION — URL Cleaner
==========================
Extracts the root domain from long source URLs.

Problem: Serper web search returns URLs like:
  - https://www.yelp.com/biz/some-business-dallas
  - https://www.facebook.com/businessname
  - https://www.google.com/maps/place/...

These are NOT the business's website — they're links to directory/profile pages.
The actual website should be extracted from the URL, or marked as ABSENT if
it's a social media / directory URL.

This module:
  1. Extracts the root domain from any URL (https://www.example.com/path → example.com)
  2. Detects and skips aggregator/social URLs (yelp, facebook, etc.)
  3. Reconstructs a clean website URL (https://example.com)
"""

import re
from urllib.parse import urlparse, urlunparse
from typing import Optional


# Domains that are NOT business websites — they're directories or social media.
# If a URL points to one of these, the business doesn't have a discoverable
# website from this source.
AGGREGATOR_DOMAINS = {
    # Social media
    "facebook.com", "fb.com", "instagram.com", "twitter.com", "x.com",
    "linkedin.com", "youtube.com", "tiktok.com", "pinterest.com",
    "snapchat.com", "reddit.com", "tumblr.com",
    # Review/directory sites
    "yelp.com", "yelp.co.uk", "tripadvisor.com", "tripadvisor.co.uk",
    "google.com", "google.co.uk", "google maps", "maps.google.com",
    "bbb.org", "trustpilot.com", "houzz.com", "angi.com", "angieslist.com",
    "yellowpages.com", "yell.com", "foursquare.com", "manta.com",
    # Marketplaces
    "amazon.com", "amazon.co.uk", "etsy.com", "ebay.com", "walmart.com",
    "shopify.com", "myshopify.com", "app.shopify.com",
    # Listing sites
    "wikipedia.org", "wikidata.org", "crunchbase.com", "bloomberg.com",
    # Search engines
    "bing.com", "yahoo.com", "duckduckgo.com",
    # Directories by industry
    "zocdoc.com", "healthgrades.com", "avvo.com", "martindale.com",
    "zillow.com", "realtor.com", "redfin.com", "trulia.com",
    "opentable.com", "booking.com", "expedia.com",
}


def is_aggregator_url(url: str) -> bool:
    """Check if a URL points to a social media or directory site."""
    if not url or url == "ABSENT":
        return False
    try:
        parsed = urlparse(url if url.startswith("http") else "https://" + url)
        domain = (parsed.hostname or "").lower()
        if domain.startswith("www."):
            domain = domain[4:]
        for agg in AGGREGATOR_DOMAINS:
            if domain == agg or domain.endswith("." + agg):
                return True
        return False
    except Exception:
        return False


def extract_root_website(url: str) -> str:
    """
    Extract the root website URL from any URL.

    Examples:
      https://www.example.com/some/long/path → https://example.com
      https://yelp.com/biz/business → ABSENT (it's a directory URL)
      https://www.facebook.com/business → ABSENT (it's social media)

    Returns:
      Clean website URL (https://example.com) or "ABSENT" if it's an aggregator.
    """
    if not url or url == "ABSENT":
        return "ABSENT"

    try:
        # Ensure URL has a protocol
        if not url.startswith("http"):
            url = "https://" + url

        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()

        if not hostname:
            return "ABSENT"

        # Strip www. prefix
        if hostname.startswith("www."):
            hostname = hostname[4:]

        # Check if this is an aggregator/social URL
        for agg in AGGREGATOR_DOMAINS:
            if hostname == agg or hostname.endswith("." + agg):
                return "ABSENT"

        # Reconstruct clean URL: https://hostname
        return f"https://{hostname}"
    except Exception:
        return "ABSENT"


def clean_website_url(url: str) -> str:
    """
    Clean a website URL for display.
    Strips protocol and trailing slash for a cleaner look.

    Examples:
      https://www.example.com/ → example.com
      https://example.com/path → example.com
    """
    if not url or url == "ABSENT":
        return ""
    try:
        if not url.startswith("http"):
            url = "https://" + url
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        if hostname.startswith("www."):
            hostname = hostname[4:]
        return hostname
    except Exception:
        return url
