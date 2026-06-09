"""
BAD DECISION AI — Firecrawl API Client
=======================================
FREE: 1,000 page scrapes/month, no credit card.
Scrapes websites WITH JavaScript rendering. Returns clean Markdown.
"""

import httpx
from typing import Optional, Dict, Any
from config import FIRECRAWL_API_KEY


FIRECRAWL_SCRAPE_URL = "https://api.firecrawl.dev/v1/scrape"


async def firecrawl_scrape(url: str) -> Optional[str]:
    """
    Scrape a URL using Firecrawl (JS rendering included).
    Returns the page content as clean Markdown text, or None if failed.
    """
    if not FIRECRAWL_API_KEY:
        print("[FIRECRAWL] No API key configured — skipping")
        return None

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                FIRECRAWL_SCRAPE_URL,
                headers={
                    "Authorization": f"Bearer {FIRECRAWL_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "url": url,
                    "formats": ["markdown"],
                },
            )

            if response.status_code != 200:
                print(f"[FIRECRAWL] Scrape error: {response.status_code} for {url}")
                return None

            data = response.json()
            markdown = data.get("data", {}).get("markdown", "")

            if markdown:
                print(f"[FIRECRAWL] Scraped {url}: {len(markdown)} chars")
                return markdown
            else:
                print(f"[FIRECRAWL] Empty result for {url}")
                return None

    except Exception as e:
        print(f"[FIRECRAWL] Error for {url}: {e}")
        return None
