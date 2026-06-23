"""
BAD DECISION — Ecommerce Platform Detector
===========================================
Detects what ecommerce platform a website uses by checking
page source for specific patterns. Free (just HTTP GET + regex).

Supported platforms:
  - Shopify (check /products.json endpoint)
  - WooCommerce (check for 'woocommerce' in HTML)
  - BigCommerce (check for 'bigcommerce' in HTML)
  - Magento (check for 'magento' in HTML)
  - Squarespace (check for 'sqsp' in HTML)
  - Wix (check for 'wix.com' in HTML)

Usage:
    platform = await detect_ecommerce_platform("https://mystore.com")
    # platform = "Shopify"
"""
import httpx
import re
from typing import Optional, Dict, Any

SOURCE_TIMEOUT = 10

# Platform detection patterns
PLATFORM_PATTERNS = {
    "Shopify": [
        r"cdn\.shopify\.com",
        r"shopify\.theme",
        r"Shopify\.shop",
        r"myshopify\.com",
    ],
    "WooCommerce": [
        r"woocommerce",
        r"wp-content/plugins/woocommerce",
    ],
    "BigCommerce": [
        r"bigcommerce\.com",
        r"cdn11\.bigcommerce\.com",
        r"store-\d+\.mybigcommerce\.com",
    ],
    "Magento": [
        r"Magento",
        r"mage/cookies",
        r"skin/frontend",
    ],
    "Squarespace": [
        r"squarespace",
        r"static1\.sqspcdn\.com",
    ],
    "Wix": [
        r"wix\.com",
        r"static\.wixstatic\.com",
    ],
}


async def detect_ecommerce_platform(website_url: str) -> Optional[str]:
    """
    Detect what ecommerce platform a website uses.

    Args:
        website_url: Full URL (e.g., "https://mystore.com")

    Returns:
        Platform name ("Shopify", "WooCommerce", etc.) or None if not detected.
    """
    if not website_url or website_url == "ABSENT":
        return None

    # Normalize URL — strip query params (Serper adds ?srsltid=... which breaks detection)
    if not website_url.startswith("http"):
        website_url = "https://" + website_url
    from urllib.parse import urlparse
    parsed = urlparse(website_url)
    website_url = f"{parsed.scheme}://{parsed.hostname}"

    try:
        async with httpx.AsyncClient(timeout=SOURCE_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(
                website_url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; BadDecisionBot/1.0)"},
            )

            if response.status_code != 200:
                return None

            html = response.text[:50000]  # Only check first 50K chars

            # Check each platform's patterns
            for platform, patterns in PLATFORM_PATTERNS.items():
                for pattern in patterns:
                    if re.search(pattern, html, re.IGNORECASE):
                        return platform

            # Also try Shopify's /products.json endpoint (definitive check)
            is_shopify = await _check_shopify_products_endpoint(website_url)
            if is_shopify:
                return "Shopify"

            return None

    except Exception:
        return None


async def _check_shopify_products_endpoint(website_url: str) -> bool:
    """Check if /products.json returns valid JSON (definitive Shopify check)."""
    try:
        # Strip query params — Serper adds ?srsltid=... which breaks the endpoint
        from urllib.parse import urlparse
        parsed = urlparse(website_url)
        base_url = f"{parsed.scheme}://{parsed.hostname}"
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(
                f"{base_url}/products.json",
                headers={"User-Agent": "Mozilla/5.0 (compatible; BadDecisionBot/1.0)"},
            )
            if response.status_code == 200:
                data = response.json()
                if "products" in data:
                    return True
    except Exception:
        pass
    return False
