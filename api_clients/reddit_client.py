"""
BAD DECISION AI — Reddit JSON API Client
=========================================
FREE, no API key needed. Just append .json to any Reddit URL.
Returns structured post data including username, post URL, text, timestamp.
"""

import httpx
from typing import List, Dict, Any
import time


REDDIT_BASE = "https://www.reddit.com"
HEADERS = {"User-Agent": "BadDecisionAI/3.0 (Lead Intelligence Platform)"}


async def search_reddit_posts(
    query: str,
    sort: str = "new",
    time_filter: str = "hour",
    limit: int = 25,
) -> List[Dict[str, Any]]:
    """
    Search Reddit for recent posts matching a query.
    Returns structured data: username, post_url, title, body, timestamp, subreddit.
    """
    try:
        async with httpx.AsyncClient(timeout=15, headers=HEADERS) as client:
            url = f"{REDDIT_BASE}/search.json"
            params = {
                "q": query,
                "sort": sort,
                "t": time_filter,
                "limit": limit,
                "type": "link",
            }

            response = await client.get(url, params=params)

            if response.status_code != 200:
                print(f"[REDDIT] Search error: {response.status_code}")
                return []

            data = response.json()
            posts = []

            for child in data.get("data", {}).get("children", []):
                post = child.get("data", {})

                username = post.get("author", "ABSENT")
                post_url = REDDIT_BASE + post.get("permalink", "")
                title = post.get("title", "")
                body = post.get("selftext", "")
                created_utc = post.get("created_utc", 0)
                subreddit = post.get("subreddit", "")
                upvotes = post.get("score", 0)
                num_comments = post.get("num_comments", 0)

                hours_ago = (time.time() - created_utc) / 3600 if created_utc else None

                posts.append({
                    "poster_name": f"u/{username}" if username != "ABSENT" else "ABSENT",
                    "poster_profile_url": f"{REDDIT_BASE}/user/{username}" if username != "ABSENT" else "ABSENT",
                    "platform": "Reddit",
                    "post_url": post_url,
                    "post_title": title,
                    "intent_text": body or title,
                    "post_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(created_utc)) if created_utc else "ABSENT",
                    "community_or_group": f"r/{subreddit}" if subreddit else "ABSENT",
                    "upvotes": upvotes,
                    "comment_count": num_comments,
                    "hours_ago": round(hours_ago, 1) if hours_ago else None,
                })

            print(f"[REDDIT] Found {len(posts)} posts for '{query}'")
            return posts

    except Exception as e:
        print(f"[REDDIT] Error: {e}")
        return []
