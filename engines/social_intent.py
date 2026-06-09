"""
BAD DECISION AI — Engine 4: Social Intent Radar (6-Platform)
=============================================================
PIPELINE:
1. Reddit JSON API → Direct structured search (FREE)
2. Serper.dev → site:facebook.com, site:instagram.com, site:linkedin.com, site:discord.com, site:skool.com
3. Merge, dedup, classify intent
4. Filter to last 60 minutes where timestamp available
5. NO validation gates (social posts have no website)

V3: Accepts location dict for geo-targeted Serper searches.
"""

import asyncio
import re
import time
from typing import List, Dict, Any
from datetime import datetime, timedelta

from api_clients.reddit_client import search_reddit_posts
from api_clients.serper import serper_site_search
from ai.deepseek_middleware import execute_llm_payload, DEEPSEEK_SCOUT_MODEL
from dedup.hash_dedup import compute_hash, check_duplicate


# Intent keywords for filtering
BUYING_INTENT_KEYWORDS = [
    "looking for", "need", "hiring", "recommend", "recommendation",
    "help me", "can anyone", "seeking", "searching for", "any good",
    "who knows", "referral", "any suggestions", "desperately need",
]

SELLING_INTENT_KEYWORDS = [
    "we offer", "our services", "contact us for", "hire us",
    "book now", "free consultation", "check out our",
]


async def run_social_intent(query: str, user_tier: str = "free", location: dict = None) -> List[Dict[str, Any]]:
    leads = []

    # Default location
    if location is None:
        location = {}

    # PHASE 1: DISCOVERY (parallel — all 6 platforms)
    print(f"[SOCIAL_INTENT] Discovery phase: '{query}' (location: {location})")

    intent_query = f'{query} {" OR ".join(BUYING_INTENT_KEYWORDS[:4])}'

    reddit_task = search_reddit_posts(intent_query, sort="new", time_filter="hour", limit=25)
    facebook_task = serper_site_search("facebook.com", f'"{query}" "looking for" OR "need" OR "recommend"', num_results=10, location=location)
    instagram_task = serper_site_search("instagram.com", f'"{query}" "need" OR "looking for"', num_results=10, location=location)
    linkedin_task = serper_site_search("linkedin.com/posts", f'"{query}" "looking for" OR "hiring"', num_results=10, location=location)
    discord_task = serper_site_search("discord.com", f'"{query}"', num_results=10, location=location)
    skool_task = serper_site_search("skool.com", f'"{query}"', num_results=10, location=location)

    reddit_results, facebook_results, instagram_results, linkedin_results, discord_results, skool_results = await asyncio.gather(
        reddit_task, facebook_task, instagram_task, linkedin_task, discord_task, skool_task,
        return_exceptions=True
    )

    reddit_results = reddit_results if isinstance(reddit_results, list) else []
    facebook_results = facebook_results if isinstance(facebook_results, list) else []
    instagram_results = instagram_results if isinstance(instagram_results, list) else []
    linkedin_results = linkedin_results if isinstance(linkedin_results, list) else []
    discord_results = discord_results if isinstance(discord_results, list) else []
    skool_results = skool_results if isinstance(skool_results, list) else []

    print(f"[SOCIAL_INTENT] Reddit: {len(reddit_results)} | FB: {len(facebook_results)} | IG: {len(instagram_results)} | LI: {len(linkedin_results)} | Discord: {len(discord_results)} | Skool: {len(skool_results)}")

    # Process Reddit results (already structured)
    all_posts = []
    seen_urls = set()

    for post in reddit_results:
        post_url = post.get("post_url", "")
        if post_url and post_url not in seen_urls:
            seen_urls.add(post_url)
            all_posts.append(post)

    # Process Serper results for each platform
    platform_map = {
        "facebook.com": ("Facebook", "📘"),
        "instagram.com": ("Instagram", "📷"),
        "linkedin.com": ("LinkedIn", "💼"),
        "discord.com": ("Discord", "💬"),
        "skool.com": ("Skool", "🎓"),
    }

    for results, domain_key in [
        (facebook_results, "facebook.com"),
        (instagram_results, "instagram.com"),
        (linkedin_results, "linkedin.com"),
        (discord_results, "discord.com"),
        (skool_results, "skool.com"),
    ]:
        platform_name, platform_icon = platform_map.get(domain_key, ("Unknown", "🔍"))

        for result in results:
            link = result.get("link", "")
            title = result.get("title", "")
            snippet = result.get("snippet", "")

            if link and link not in seen_urls:
                seen_urls.add(link)

                # Parse poster name from URL
                poster_name = _extract_poster_name(link, platform_name)

                all_posts.append({
                    "poster_name": poster_name,
                    "poster_profile_url": _extract_profile_url(link, platform_name),
                    "platform": platform_name,
                    "post_url": link,
                    "post_title": title,
                    "intent_text": snippet,
                    "post_timestamp": "ABSENT",
                    "community_or_group": _extract_community(result, platform_name),
                    "hours_ago": None,
                })

    print(f"[SOCIAL_INTENT] {len(all_posts)} unique social posts found")

    # PHASE 2: FILTER FOR INTENT
    filtered_posts = []
    for post in all_posts:
        text = f"{post.get('post_title', '')} {post.get('intent_text', '')}".lower()

        # Must have buying intent keywords
        has_buying_intent = any(kw in text for kw in BUYING_INTENT_KEYWORDS)
        has_selling_intent = any(kw in text for kw in SELLING_INTENT_KEYWORDS)

        if has_buying_intent and not has_selling_intent:
            post["intent_type"] = _classify_intent(text)
            post["time_sensitivity"] = _assess_urgency(text)
            filtered_posts.append(post)

    print(f"[SOCIAL_INTENT] {len(filtered_posts)} posts with buying intent")

    # PHASE 3: BUILD LEADS
    for post in filtered_posts[:50]:
        post_url = post.get("post_url", "")
        poster_name = post.get("poster_name", "ABSENT")

        url_to_hash = post_url if post_url else poster_name
        domain_hash = compute_hash(url_to_hash)

        is_dup, cached_data = await check_duplicate(domain_hash)
        if is_dup and cached_data:
            leads.append(cached_data)
            continue

        platform = post.get("platform", "Unknown")

        lead = {
            "domain_hash": domain_hash,
            "company_name": poster_name,  # Username for social
            "website_url": post_url,  # Post URL instead of website
            "phone": "ABSENT",
            "verified_email": "ABSENT",
            "dm_name": poster_name,
            "dm_position": "ABSENT",
            "engine_type": "social_intent",
            "engine_data": {
                "poster_name": poster_name,
                "poster_profile_url": post.get("poster_profile_url", "ABSENT"),
                "platform": platform,
                "post_url": post_url,
                "post_title": post.get("post_title", "ABSENT"),
                "intent_text": post.get("intent_text", "ABSENT"),
                "post_timestamp": post.get("post_timestamp", "ABSENT"),
                "community_or_group": post.get("community_or_group", "ABSENT"),
                "intent_type": post.get("intent_type", "ABSENT"),
                "time_sensitivity": post.get("time_sensitivity", "medium"),
                "hours_ago": post.get("hours_ago"),
                "suggested_response": _suggest_response(platform, post.get("intent_type", "")),
                "outreach_method": _get_outreach_method(platform),
            },
            "discovery_source": "reddit_api" if platform == "Reddit" else "serper",
            "email_source": "ABSENT",
        }

        leads.append(lead)

    print(f"[SOCIAL_INTENT] Completed: {len(leads)} intent leads")
    return leads


def _extract_poster_name(url: str, platform: str) -> str:
    """Try to extract a poster name from a URL."""
    if platform == "Facebook":
        # facebook.com/username or facebook.com/groups/name/posts/...
        parts = url.rstrip('/').split('/')
        for part in parts:
            if part and part not in ['https:', 'http:', 'www.facebook.com', 'facebook.com', 'groups', 'posts', 'photos']:
                return part
    elif platform == "Instagram":
        # instagram.com/username/p/ABC123
        parts = url.rstrip('/').split('/')
        if len(parts) > 3:
            return f"@{parts[3]}"
    elif platform == "LinkedIn":
        parts = url.rstrip('/').split('/')
        if len(parts) > 4:
            return parts[4].replace('-', ' ').title()
    elif platform == "Discord":
        return "Discord Community"
    elif platform == "Skool":
        parts = url.rstrip('/').split('/')
        if len(parts) > 3:
            return parts[3].replace('-', ' ').title()
    return "ABSENT"


def _extract_profile_url(url: str, platform: str) -> str:
    """Try to extract a profile URL from a post URL."""
    if platform == "Reddit":
        return url.split('/comments/')[0] if '/comments/' in url else url
    elif platform == "Instagram":
        parts = url.rstrip('/').split('/')
        if len(parts) > 3:
            return f"https://instagram.com/{parts[3]}"
    elif platform == "LinkedIn":
        return url
    return "ABSENT"


def _extract_community(result: Dict, platform: str) -> str:
    """Extract community/group name from search result."""
    title = result.get("title", "")
    if " - " in title:
        return title.split(" - ")[-1].strip()
    elif " | " in title:
        return title.split(" | ")[-1].strip()
    return "ABSENT"


def _classify_intent(text: str) -> str:
    """Classify the type of buying intent."""
    text = text.lower()
    if any(kw in text for kw in ["hire", "hiring", "job", "position"]):
        return "hiring"
    elif any(kw in text for kw in ["buy", "purchase", "price", "cost"]):
        return "buying"
    elif any(kw in text for kw in ["recommend", "suggestion", "any good"]):
        return "recommendation_seeking"
    elif any(kw in text for kw in ["problem", "broken", "fix", "issue", "error"]):
        return "problem"
    return "general_intent"


def _assess_urgency(text: str) -> str:
    """Assess how urgent the intent signal is."""
    text = text.lower()
    if any(kw in text for kw in ["asap", "urgent", "emergency", "desperately", "immediately"]):
        return "high"
    elif any(kw in text for kw in ["need", "looking for", "help"]):
        return "medium"
    return "low"


def _suggest_response(platform: str, intent_type: str) -> str:
    """Suggest how the user should respond to this lead."""
    if platform == "Reddit":
        return "Comment on their post offering your services"
    elif platform == "Facebook":
        return "Comment on their post or message them in the group"
    elif platform == "Instagram":
        return "DM them or comment on their post"
    elif platform == "LinkedIn":
        return "Connect and send a message"
    elif platform == "Discord":
        return "Join the server and engage in relevant channels"
    elif platform == "Skool":
        return "Join the community and offer help"
    return "Reach out on the platform"


def _get_outreach_method(platform: str) -> str:
    """Get the recommended outreach method."""
    if platform in ("Discord", "Skool"):
        return "join_community"
    elif platform == "LinkedIn":
        return "connect_and_message"
    else:
        return "reply_on_platform"
