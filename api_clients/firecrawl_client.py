"""
BAD DECISION AI — Firecrawl API Client
======================================
1,000 free credits/month. Used for scraping JavaScript-heavy pages.
"""

import httpx
from typing import Optional, Dict, Any
from config import FIRECRAWL_API_KEY


async def firecrawl_scrape(url: str) -> Optional[Dict[str, Any]]:
    """Scrape a URL using Firecrawl (handles JavaScript rendering)."""
    if not FIRECRAWL_API_KEY:
        return None

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                "https://api.firecrawl.dev/v0/scrape",
                headers={
                    "Authorization": f"Bearer {FIRECRAWL_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={"url": url},
            )

            if response.status_code == 200:
                data = response.json().get("data", {})
                return {
                    "html": data.get("html", ""),
                    "text": data.get("content", ""),
                    "url": url,
                }
            return None

    except Exception as e:
        print(f"[FIRECRAWL] Error: {e}")
        return None
