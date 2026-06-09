"""
BAD DECISION AI — Engine 4: Social Intent Radar
================================================
Finds people actively expressing buying intent on social platforms.
Uses Reddit .json API (free) + Serper.dev for Facebook, Instagram,
LinkedIn, Discord, Skool searches.

Returns: poster_name, poster_profile_url, post_url, platform,
intent_text, community_or_group, intent_type, time_sensitivity,
suggested_response, outreach_method
"""

import json
from datetime import datetime, timedelta
from typing import List, Dict, Any
from scraping.stealth_fetcher import (
    stealth_fetch, extract_text_from_html, build_reddit_json_url,
)
from ai.deepseek_middleware import execute_llm_payload, DEEPSEEK_SCOUT_MODEL
from dedup.hash_dedup import compute_hash, check_duplicate


async def run_social_intent(query: str, user_tier: str = "free", location: str = "") -> List[Dict[str, Any]]:
    """Find people actively posting about needing help with the query topic."""
    leads = []

    now = datetime.utcnow()
    time_str = (now - timedelta(minutes=60)).strftime("%Y-%m-%d %H:%M UTC")

    print(f"[SOCIAL_INTENT] Fetching social intent for '{query}' in '{location}'")
    scraped_texts = []
    reddit_posts = []

    # Source 1: Reddit .json API (free, no API key)
    try:
        from api_clients.reddit_client import search_reddit
        reddit_posts = await search_reddit(query)
        if reddit_posts:
            reddit_text = "\n".join(
                f"User: {p.get('author', 'N/A')} | Subreddit: {p.get('subreddit', 'N/A')} | "
                f"Title: {p.get('title', 'N/A')} | Body: {p.get('selftext', '')[:200]} | "
                f"URL: {p.get('url', 'N/A')} | Created: {p.get('created_utc', 'N/A')}"
                for p in reddit_posts[:25]
            )
            scraped_texts.append({"source": "Reddit API", "content": reddit_text})
            print(f"[SOCIAL_INTENT] Reddit: {len(reddit_posts)} posts")
    except Exception as e:
        print(f"[SOCIAL_INTENT] Reddit API error: {e}")

    # Source 2: Serper.dev for Facebook, Instagram, LinkedIn, Discord, Skool
    try:
        from api_clients.serper import serper_search
        platforms = [
            (f"{query} site:facebook.com \"looking for\" OR \"need help\" OR \"hiring\" {location}", "Facebook"),
            (f"{query} site:instagram.com {location}", "Instagram"),
            (f"{query} site:linkedin.com \"looking for\" OR \"hiring\" OR \"seeking\" {location}", "LinkedIn"),
            (f"{query} site:discord.com OR site:discord.gg {location}", "Discord"),
            (f"{query} site:skool.com {location}", "Skool"),
        ]
        for search_query, platform_name in platforms:
            results = await serper_search(search_query, num_results=10)
            if results:
                platform_text = "\n".join(
                    f"Title: {r.get('title', '')} | Snippet: {r.get('snippet', '')} | URL: {r.get('link', '')}"
                    for r in results
                )
                scraped_texts.append({"source": f"Serper ({platform_name})", "content": platform_text})
                print(f"[SOCIAL_INTENT] {platform_name}: {len(results)} results")
    except Exception as e:
        print(f"[SOCIAL_INTENT] Serper error: {e}")

    # Source 3: Scrapling fallback for Reddit
    if not reddit_posts:
        reddit_url = build_reddit_json_url(query)
        reddit_result = await stealth_fetch(reddit_url)
        if reddit_result:
            text = extract_text_from_html(reddit_result["html"])
            if text:
                scraped_texts.append({"source": "Reddit (Scrapling)", "content": text})

    if not scraped_texts:
        print(f"[SOCIAL_INTENT] All sources failed")
        return []

    combined_text = "\n\n".join(f"--- SOURCE: {s['source']} ---\n{s['content']}" for s in scraped_texts)

    # PHASE 2: DEEPSEEK
    print(f"[SOCIAL_INTENT] DeepSeek structuring")
    structure_prompt = f"""
    You are a social media intelligence researcher. Below is REAL TEXT about people
    actively seeking help, looking to hire, or expressing buying intent related to: "{query}" in "{location}"

    Extract REAL people and posts. Do NOT invent. Only extract clearly mentioned people/posts.

    HARD RULES:
    - Person must be actively SEEKING help or expressing NEED
    - We want BUYERS, not sellers
    - Posts should be recent if timestamps are available

    SCRAPED CONTENT:
    {combined_text[:12000]}

    For each person/post, provide:
    - poster_name: Person's full name or username
    - platform: Which platform (Reddit, Facebook, Instagram, LinkedIn, Discord, Skool)
    - poster_profile_url: Direct link to profile (or "ABSENT")
    - post_url: Direct link to the post (or "ABSENT")
    - intent_text: The exact text showing intent
    - community_or_group: Subreddit, group, or community name (or "ABSENT")
    - intent_type: Type of intent ("seeking_service", "hiring", "looking_for_recommendation", "complaining", "comparing_options")

    Return JSON with "people" array. Up to 25.
    """

    try:
        response = await execute_llm_payload({
            "model": DEEPSEEK_SCOUT_MODEL,
            "messages": [
                {"role": "system", "content": "Precise data extractor. Only REAL people from text. Never invent. Valid JSON. 'ABSENT' for missing."},
                {"role": "user", "content": structure_prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        })
        content = response.get("choices", [{}])[0].get("message", {}).get("content", "{}")
        parsed = json.loads(content)
        people = parsed.get("people", parsed.get("results", []))
        if isinstance(parsed, list):
            people = parsed
    except Exception as e:
        print(f"[SOCIAL_INTENT] DeepSeek error: {e}")
        people = []

    print(f"[SOCIAL_INTENT] {len(people)} candidates")

    # PHASE 3: Process each person
    for person in people[:25]:
        poster_name = person.get("poster_name", person.get("name", "ABSENT"))
        platform = person.get("platform", "ABSENT")
        poster_profile_url = person.get("poster_profile_url", person.get("profile_url", "ABSENT"))
        post_url = person.get("post_url", "ABSENT")
        intent_text = person.get("intent_text", "ABSENT")
        community_or_group = person.get("community_or_group", "ABSENT")
        intent_type = person.get("intent_type", "seeking_service")

        if poster_name == "ABSENT" or not poster_name:
            continue

        url_to_hash = post_url if post_url != "ABSENT" else f"{poster_name}_{platform}"
        domain_hash = compute_hash(url_to_hash)

        is_dup, cached_data = await check_duplicate(domain_hash)
        if is_dup and cached_data:
            leads.append(cached_data)
            continue

        # Determine time sensitivity
        time_sensitivity = "medium"
        intent_lower = intent_text.lower() if intent_text else ""
        if any(w in intent_lower for w in ["urgent", "asap", "immediately", "emergency", "today"]):
            time_sensitivity = "high"
        elif any(w in intent_lower for w in ["thinking about", "considering", "someday", "maybe"]):
            time_sensitivity = "low"

        # Determine outreach method
        outreach_method = "dm"
        if platform.lower() in ("linkedin",):
            outreach_method = "connection_request"
        elif platform.lower() in ("reddit",):
            outreach_method = "dm_or_comment"
        elif platform.lower() in ("facebook", "instagram"):
            outreach_method = "dm"

        # Generate suggested response
        suggested = _generate_suggested_response(poster_name, intent_text, platform, intent_type)

        lead = {
            "domain_hash": domain_hash,
            "company_name": poster_name,
            "website_url": poster_profile_url,
            "dm_name": poster_name,
            "dm_position": "ABSENT",
            "verified_email": "ABSENT",
            "is_catchall": False,
            "poster_name": poster_name,
            "poster_profile_url": poster_profile_url,
            "post_url": post_url,
            "platform": platform,
            "intent_text": intent_text,
            "community_or_group": community_or_group,
            "intent_type": intent_type,
            "time_sensitivity": time_sensitivity,
            "suggested_response": suggested,
            "outreach_method": outreach_method,
        }

        leads.append(lead)

    return leads


def _generate_suggested_response(name: str, intent_text: str, platform: str, intent_type: str) -> str:
    """Generate a suggested outreach response based on the intent."""
    first_name = name.split()[0] if name and name != "ABSENT" else "there"

    if intent_type == "hiring":
        return f"Hi {first_name}, I saw you're looking to hire. I'd love to discuss how we can help. Would you be open to a quick chat this week?"
    elif intent_type == "seeking_service":
        return f"Hi {first_name}, I noticed you're looking for help. We specialize in exactly that. Want me to share some examples of our work?"
    elif intent_type == "looking_for_recommendation":
        return f"Hi {first_name}, I saw your post asking for recommendations. We'd be happy to help — here's what makes us different..."
    elif intent_type == "complaining":
        return f"Hi {first_name}, sorry to hear about your experience. We take a different approach — would you like to learn more?"
    else:
        return f"Hi {first_name}, I came across your post and thought we might be able to help. Would you be open to a brief conversation?"
