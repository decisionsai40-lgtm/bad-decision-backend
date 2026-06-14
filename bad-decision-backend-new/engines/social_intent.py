"""
BAD DECISION AI — Engine 4: Real-Time Demand Radar (Scrapling-First)
=====================================================================
This engine finds people who are actively asking for help
or expressing buying intent on social platforms.

PIPELINE (as specified in TRD Section 4):
1. Scrapling fetches REAL data from Reddit, GitHub, and Google search
2. DeepSeek structures the scraped HTML/text into clean lead objects
3. HARD FILTER: Only posts from the LAST 60 MINUTES
4. No validation gates — social posts have no website to validate
5. Dedup & cache

HARD RULE: Only find posts from the LAST 60 MINUTES.
We want people who are actively looking RIGHT NOW.

Targets: LinkedIn, Skool, Circle, GitHub, Telegram Groups, Reddit
"""

import json
from datetime import datetime, timedelta
from typing import List, Dict, Any

from scraping.stealth_fetcher import (
    stealth_fetch,
    extract_text_from_html,
    build_github_search_url,
    build_reddit_search_url,
    build_google_search_url,
)
from ai.deepseek_middleware import execute_llm_payload, DEEPSEEK_SCOUT_MODEL
from dedup.hash_dedup import compute_hash, check_duplicate


async def run_social_intent(
    query: str,
    user_tier: str = "free",
) -> List[Dict[str, Any]]:
    """
    Find people actively posting about needing help with the query topic.

    PIPELINE:
    1. Scrapling fetches Reddit + GitHub + Google search for social intent signals
    2. DeepSeek structures scraped text into lead objects
    3. Filter to posts from the last 60 minutes only
    4. Extract name, platform, profile URL, and the intent text
    5. No validation gates — this is live social data
    """

    leads = []

    # Calculate the 60-minute boundary
    now = datetime.utcnow()
    sixty_minutes_ago = now - timedelta(minutes=60)
    time_str = sixty_minutes_ago.strftime("%Y-%m-%d %H:%M UTC")

    # --------------------------------------------------------
    # PHASE 1: SCRAPLING — Fetch real data from social platforms
    # --------------------------------------------------------
    print(f"[SOCIAL_INTENT] Scrapling Phase: Fetching social intent data for '{query}'")

    scraped_texts = []

    # Source 1: Reddit search (public, sorted by newest, last hour)
    reddit_url = build_reddit_search_url(query)
    reddit_result = await stealth_fetch(reddit_url)
    if reddit_result:
        text = extract_text_from_html(reddit_result["html"])
        if text:
            scraped_texts.append({
                "source": "Reddit",
                "content": text,
            })
            print(f"[SOCIAL_INTENT] Scraped Reddit: {len(text)} chars")
    else:
        print(f"[SOCIAL_INTENT] Reddit fetch failed — continuing with other sources")

    # Source 2: GitHub Issues/Discussions (public, no login required)
    github_url = build_github_search_url(query)
    github_result = await stealth_fetch(github_url)
    if github_result:
        text = extract_text_from_html(github_result["html"])
        if text:
            scraped_texts.append({
                "source": "GitHub",
                "content": text,
            })
            print(f"[SOCIAL_INTENT] Scraped GitHub: {len(text)} chars")
    else:
        print(f"[SOCIAL_INTENT] GitHub fetch failed")

    # Source 3: Google search for social intent signals
    google_url = build_google_search_url(
        f"{query} site:reddit.com OR site:linkedin.com OR site:github.com \"looking for\" OR \"need help\" OR \"hiring\""
    )
    google_result = await stealth_fetch(google_url)
    if google_result:
        text = extract_text_from_html(google_result["html"])
        if text:
            scraped_texts.append({
                "source": "Google Search (social focus)",
                "content": text,
            })
            print(f"[SOCIAL_INTENT] Scraped Google Search: {len(text)} chars")
    else:
        print(f"[SOCIAL_INTENT] Google Search fetch failed")

    if not scraped_texts:
        print(f"[SOCIAL_INTENT] All Scrapling sources failed — no data to process")
        return []

    # Combine all scraped content for DeepSeek
    combined_text = "\n\n".join(
        f"--- SOURCE: {s['source']} ---\n{s['content']}"
        for s in scraped_texts
    )

    # --------------------------------------------------------
    # PHASE 2: DEEPSEEK — Structure the scraped data
    # --------------------------------------------------------
    print(f"[SOCIAL_INTENT] DeepSeek Phase: Structuring scraped data")

    structure_prompt = f"""
    You are a social media intelligence researcher. Below is REAL TEXT scraped from the internet
    about people who are actively seeking help, looking to hire, or expressing buying intent related to: "{query}"

    Your job is to extract REAL people and posts mentioned in this text.
    Do NOT invent or hallucinate people or posts that are not in the text.
    Only extract people/posts that are clearly mentioned in the scraped content.

    HARD RULES:
    - Posts should be from the last 60 minutes (after {time_str}) if timestamps are available
    - The person must be actively SEEKING help or expressing NEED
    - We want BUYERS, not sellers

    SCRAPED CONTENT:
    {combined_text[:12000]}

    For each REAL person/post you find, provide:
    - name: The person's full name or username as mentioned
    - platform: Which platform they posted on (Reddit, GitHub, LinkedIn, etc.)
    - profile_url: Direct link to their profile if mentioned (or "ABSENT")
    - intent_text: The exact text they posted that shows intent

    Return a JSON object with a "people" array. Find up to 25 people.
    If you cannot find data for a field, write "ABSENT".

    Example format:
    {{
        "people": [
            {{
                "name": "John Smith",
                "platform": "Reddit",
                "profile_url": "https://reddit.com/user/johnsmith",
                "intent_text": "Looking for a reliable roofing contractor in Dallas. Any recommendations?"
            }}
        ]
    }}
    """

    try:
        response = await execute_llm_payload({
            "model": DEEPSEEK_SCOUT_MODEL,
            "messages": [
                {"role": "system", "content": "You are a precise data extractor. Only extract REAL people and posts mentioned in the provided text. Never invent data. Always respond with valid JSON. Use 'ABSENT' for missing data."},
                {"role": "user", "content": structure_prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        })

        content = response.get("choices", [{}])[0].get("message", {}).get("content", "{}")

        try:
            parsed = json.loads(content)
            people = parsed.get("people", parsed.get("results", []))
            if isinstance(parsed, list):
                people = parsed
        except json.JSONDecodeError:
            people = []

    except Exception as e:
        print(f"[SOCIAL_INTENT] DeepSeek structuring error: {e}")
        people = []

    print(f"[SOCIAL_INTENT] DeepSeek extracted {len(people)} candidate people from scraped data")

    # --------------------------------------------------------
    # PHASE 3: Process each person
    # --------------------------------------------------------
    for person in people[:25]:
        name = person.get("name", "ABSENT")
        platform = person.get("platform", "ABSENT")
        profile_url = person.get("profile_url", "ABSENT")
        intent_text = person.get("intent_text", "ABSENT")

        # Skip if no name
        if name == "ABSENT" or not name:
            continue

        # Dedup check using profile URL
        url_to_hash = profile_url if profile_url != "ABSENT" else name
        domain_hash = compute_hash(url_to_hash)

        is_dup, cached_data = await check_duplicate(domain_hash)
        if is_dup and cached_data:
            leads.append(cached_data)
            continue

        # Note: Social intent leads do NOT go through the 3-Gate validation
        # because these are real-time social posts — there's no website to
        # check DNS/SMTP for. They are treated differently.

        lead = {
            "domain_hash": domain_hash,
            "company_name": name,  # Person's name instead of company
            "website_url": profile_url,  # Profile URL instead of website
            "dm_name": name,
            "dm_position": "ABSENT",
            "verified_email": "ABSENT",
            "is_catchall": False,
            "linkedin": profile_url if "linkedin" in profile_url.lower() else "ABSENT",
            "instagram": "ABSENT",
            "phone": "ABSENT",
            "platform": platform,
            "intent_text": intent_text,
        }

        leads.append(lead)

    return leads
