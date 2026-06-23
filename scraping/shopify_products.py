"""
BAD DECISION — Shopify Product Catalog Fetcher
================================================
Fetches the public product catalog from Shopify stores via /products.json.
This is a free, public API that every Shopify store exposes.

Returns: product count, categories, average price, price range, currency.

Usage:
    data = await fetch_shopify_products("https://mystore.com")
    # data = {"product_count": 42, "categories": ["Shirts", "Pants"], ...}
"""
import httpx
from typing import Dict, Any, List, Optional

SOURCE_TIMEOUT = 10


async def fetch_shopify_products(website_url: str) -> Optional[Dict[str, Any]]:
    """
    Fetch product catalog from a Shopify store.

    Args:
        website_url: Full URL of the store (e.g., "https://mystore.com")

    Returns:
        Dict with: product_count, product_categories, average_price,
        price_range, store_currency, uses_inventory_tracking
        Returns None if not a Shopify store or fetch fails.
    """
    if not website_url or website_url == "ABSENT":
        return None

    # Normalize URL — strip query params and fragments
    # Serper returns URLs like https://example.com/?srsltid=AfmBOor... which
    # break the products.json endpoint. We only want the root domain.
    if not website_url.startswith("http"):
        website_url = "https://" + website_url
    # Strip query string and fragment
    from urllib.parse import urlparse
    parsed = urlparse(website_url)
    base_url = f"{parsed.scheme}://{parsed.hostname}"
    # Strip trailing slash for consistency
    base_url = base_url.rstrip("/")

    try:
        async with httpx.AsyncClient(timeout=SOURCE_TIMEOUT, follow_redirects=True) as client:
            # Fetch up to 250 products (Shopify's max per page)
            response = await client.get(
                f"{base_url}/products.json",
                params={"limit": 250},
                headers={"User-Agent": "Mozilla/5.0 (compatible; BadDecisionBot/1.0)"},
            )

            if response.status_code != 200:
                return None

            data = response.json()
            products = data.get("products", [])

            if not products:
                return None

            # Extract data
            categories = set()
            prices = []
            currencies = set()
            uses_inventory = False

            for product in products:
                # Categories
                ptype = product.get("product_type", "").strip()
                if ptype:
                    categories.add(ptype)

                # Prices (from variants)
                for variant in product.get("variants", []):
                    price_str = variant.get("price")
                    if price_str:
                        try:
                            price = float(price_str)
                            if price > 0:
                                prices.append(price)
                        except (ValueError, TypeError):
                            pass

                    # Currency
                    currency = variant.get("currency")
                    if currency:
                        currencies.add(currency)

                    # Inventory tracking
                    if variant.get("inventory_management") == "shopify":
                        uses_inventory = True

            # Calculate stats
            avg_price = sum(prices) / len(prices) if prices else 0
            min_price = min(prices) if prices else 0
            max_price = max(prices) if prices else 0
            currency = list(currencies)[0] if currencies else "USD"

            result = {
                "product_count": len(products),
                "product_categories": list(categories)[:10],  # Cap at 10 categories
                "average_price": f"{avg_price:.2f}" if prices else "ABSENT",
                "price_range": f"{min_price:.2f} - {max_price:.2f}" if prices else "ABSENT",
                "store_currency": currency,
                "uses_inventory_tracking": uses_inventory,
            }

            print(f"[SHOPIFY] {base_url}: {result['product_count']} products, avg {result['average_price']} {currency}")
            return result

    except httpx.TimeoutException:
        print(f"[SHOPIFY] Timeout for {base_url}")
        return None
    except Exception as e:
        print(f"[SHOPIFY] Error for {base_url}: {e}")
        return None
