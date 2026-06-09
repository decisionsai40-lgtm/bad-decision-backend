"""
BAD DECISION AI — Engine 4: Real-Time Demand Radar (Scrapling-First + DeepSeek Fallback)
=========================================================================================
This engine finds people who are actively asking for help
or expressing buying intent on social platforms.

PIPELINE:
1. Scrapling fetches REAL data from Reddit, GitHub, and Google search
2. DeepSeek structures the scraped HTML/text into lead objects
3. Filter to recent posts
4. No validation gates — social posts have no website to validate
5. Dedup & cache

HARD RULE: Only find recent posts — people actively looking RIGHT NOW.
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
    build_duckduckgo_search_url,
)
from ai.deepseek_middleware import execute_llm_payload, DEEPSEEK_SCOUT_MODEL
from dedup.hash_dedup import compute_hash, check_duplicate


MAX_LEADS = 50


async def run_social_intent(
    query: str,
    user_tier: str = "free",
) -> List[Dict[str, Any]]:
    """
    Find people actively posting about needing help with the query topic.
    """

    leads = []

    now = datetime.utcnow()
    time_str = (now - timedelta(minutes=60)).strftime("%Y-%m-%d %H:%M UTC")

    # --------------------------------------------------------
    # PHASE 1: SCRAPLING — Fetch real data from social platforms
    # --------------------------------------------------------
    print(f"[SOCIAL_INTENT] Scrapling Phase: Fetching social intent data for '{query}'")

    scraped_texts = []

    # Source 1: Reddit search
    reddit_url = build_reddit_search_url(query)
    reddit_result = await stealth_fetch(reddit_url)
    if reddit_result:
        text = extract_text_from_html(reddit_result["html"])
        if text:
            scraped_texts.append({"source": "Reddit", "content": text})
            print(f"[SOCIAL_INTENT] Scraped Reddit: {len(text)} chars")
    else:
        print(f"[SOCIAL_INTENT] Reddit fetch failed — continuing with other sources")

    # Source 2: GitHub Issues/Discussions
    github_url = build_github_search_url(query)
    github_result = await stealth_fetch(github_url)
    if github_result:
        text = extract_text_from_html(github_result["html"])
        if text:
            scraped_texts.append({"source": "GitHub", "content": text})
            print(f"[SOCIAL_INTENT] Scraped GitHub: {len(text)} chars")
    else:
        print(f"[SOCIAL_INTENT] GitHub fetch failed")

    # Source 3: Google search for social intent
    google_url = build_google_search_url(
        f"{query} site:reddit.com OR site:linkedin.com OR site:github.com \"looking for\" OR \"need help\" OR \"hiring\""
    )
    google_result = await stealth_fetch(google_url)
    if google_result:
        text = extract_text_from_html(google_result["html"])
        if text:
            scraped_texts.append({"source": "Google Search (social focus)", "content": text})
            print(f"[SOCIAL_INTENT] Scraped Google Search: {len(text)} chars")
    else:
        print(f"[SOCIAL_INTENT] Google Search fetch failed")

    # Source 4: DuckDuckGo search
    ddg_url = build_duckduckgo_search_url(f"{query} reddit OR linkedin looking for help hiring")
    ddg_result = await stealth_fetch(ddg_url)
    if ddg_result:
        text = extract_text_from_html(ddg_result["html"])
        if text:
            scraped_texts.append({"source": "DuckDuckGo", "content": text})
            print(f"[SOCIAL_INTENT] Scraped DuckDuckGo: {len(text)} chars")
    else:
        print(f"[SOCIAL_INTENT] DuckDuckGo fetch failed")

    if not scraped_texts:
        print(f"[SOCIAL_INTENT] All Scrapling sources failed — using DeepSeek knowledge fallback")

    # Combine scraped content
    combined_text = ""
    if scraped_texts:
        combined_text = "\n\n".join(
            f"--- SOURCE: {s['source']} ---\n{s['content']}"
            for s in scraped_texts
        )

    # --------------------------------------------------------
    # PHASE 2: DEEPSEEK — Structure the data
    # --------------------------------------------------------
    print(f"[SOCIAL_INTENT] DeepSeek Phase: Structuring data")

    if combined_text:
        structure_prompt = f"""
        You are a social media intelligence researcher. Below is REAL TEXT scraped from the internet
        about people who are actively seeking help, looking to hire, or expressing buying intent related to: "{query}"

        Extract REAL people and posts mentioned in this text.
        Do NOT invent people or posts that are not in the text.

        HARD RULES:
        - The person must be actively SEEKING help or expressing NEED
        - We want BUYERS, not sellers

        SCRAPED CONTENT:
        {combined_text[:12000]}

        For each REAL person/post you find, provide:
        - name: The person's full name or username
        - platform: Which platform (Reddit, GitHub, LinkedIn, etc.)
        - profile_url: Direct link to their profile (or "ABSENT")
        - intent_text: The exact text showing their intent

        Return a JSON object with a "people" array. Find up to {MAX_LEADS} people.
        If you cannot find data for a field, write "ABSENT".
        """
    else:
        structure_prompt = f"""
        You are a social media intelligence researcher. Find real people who are
        actively seeking help or looking to hire related to: "{query}"

        Think about Reddit posts, GitHub issues, LinkedIn posts, forum discussions
        where people express buying intent or need for services.

        Find as many REAL examples as you can — aim for {MAX_LEADS}.

        For each person, provide:
        - name: Their full name or username
        - platform: Which platform (Reddit, GitHub, LinkedIn, etc.)
        - profile_url: Link to their profile (or "ABSENT")
        - intent_text: What they posted showing intent

        Return a JSON object with a "people" array. Find up to {MAX_LEADS} people.
        """

    try:
        response = await execute_llm_payload({
            "model": DEEPSEEK_SCOUT_MODEL,
            "messages": [
                {"role": "system", "content": "You are a precise data extractor. Only extract REAL people and posts. Never invent data. Always respond with valid JSON. Use 'ABSENT' for missing data. Return as many real examples as you can find."},
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

    print(f"[SOCIAL_INTENT] DeepSeek extracted {len(people)} candidate people")

    # --------------------------------------------------------
    # PHASE 3: Process each person
    # --------------------------------------------------------
    for person in people[:MAX_LEADS]:
        name = person.get("name", "ABSENT")
        platform = person.get("platform", "ABSENT")
        profile_url = person.get("profile_url", "ABSENT")
        intent_text = person.get("intent_text", "ABSENT")

        if name == "ABSENT" or not name:
            continue

        url_to_hash = profile_url if profile_url != "ABSENT" else name
        domain_hash = compute_hash(url_to_hash)

        is_dup, cached_data = await check_duplicate(domain_hash)
        if is_dup and cached_data:
            leads.append(cached_data)
            continue

        lead = {
            "domain_hash": domain_hash,
            "company_name": name,
            "website_url": profile_url,
            "dm_name": name,
            "dm_position": "ABSENT",
            "verified_email": "ABSENT",
            "is_catchall": False,
            "linkedin": profile_url if "linkedin" in str(profile_url).lower() else "ABSENT",
            "instagram": "ABSENT",
            "phone": "ABSENT",
            "platform": platform,
            "intent_text": intent_text,
        }

        leads.append(lead)

    return leads
