"""
BAD DECISION AI — Reddit API Client
====================================
Uses Reddit's .json API (free, no API key needed).
Returns structured post data.
"""

import httpx
from typing import List, Dict, Any


async def search_reddit(query: str, sort: str = "new", time: str = "hour") -> List[Dict[str, Any]]:
    """Search Reddit using the .json API. Free, no API key needed."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                "https://www.reddit.com/search.json",
                params={
                    "q": query,
                    "sort": sort,
                    "t": time,
                    "limit": 25,
                },
                headers={"User-Agent": "BadDecisionAI/1.0"},
            )

            if response.status_code == 200:
                data = response.json()
                posts = []

                for child in data.get("data", {}).get("children", []):
                    post_data = child.get("data", {})
                    posts.append({
                        "title": post_data.get("title", ""),
                        "selftext": post_data.get("selftext", ""),
                        "author": post_data.get("author", ""),
                        "subreddit": post_data.get("subreddit", ""),
                        "url": f"https://reddit.com{post_data.get('permalink', '')}",
                        "created_utc": post_data.get("created_utc", 0),
                        "score": post_data.get("score", 0),
                        "num_comments": post_data.get("num_comments", 0),
                    })

                return posts
            else:
                print(f"[REDDIT] HTTP {response.status_code}")
                return []

    except Exception as e:
        print(f"[REDDIT] Error: {e}")
        return []
