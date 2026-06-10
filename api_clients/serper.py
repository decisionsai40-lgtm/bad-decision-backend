"""
BAD DECISION AI — Serper.dev API Client
========================================
Google Search API with 2,500 free queries/month.
Used for: Google search, Maps, and site-specific searches.
"""

import httpx
from typing import List, Dict, Any, Optional
from config import SERPER_API_KEY


async def serper_search(query: str, num_results: int = 10, location: str = "") -> List[Dict[str, Any]]:
    """Search Google using Serper.dev API."""
    if not SERPER_API_KEY:
        print("[SERPER] No API key configured")
        return []

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            payload = {
                "q": query,
                "num": num_results,
            }
            if location:
                payload["location"] = location

            response = await client.post(
                "https://google.serper.dev/search",
                headers={
                    "X-API-KEY": SERPER_API_KEY,
                    "Content-Type": "application/json",
                },
                json=payload,
            )

            if response.status_code == 200:
                data = response.json()
                return data.get("organic", [])
            else:
                print(f"[SERPER] HTTP {response.status_code}")
                return []

    except Exception as e:
        print(f"[SERPER] Error: {e}")
        return []


async def serper_maps_search(query: str, location: str = "") -> List[Dict[str, Any]]:
    """Search Google Maps using Serper.dev API."""
    if not SERPER_API_KEY:
        return []

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            payload = {"q": query}
            if location:
                payload["location"] = location

            response = await client.post(
                "https://google.serper.dev/maps",
                headers={
                    "X-API-KEY": SERPER_API_KEY,
                    "Content-Type": "application/json",
                },
                json=payload,
            )

            if response.status_code == 200:
                data = response.json()
                return data.get("places", [])
            else:
                return []

    except Exception as e:
        print(f"[SERPER MAPS] Error: {e}")
        return []
