"""
BAD DECISION — Engine 4: Social Radar
======================================
This engine finds people who are actively asking for help
or expressing buying intent on social platforms.

PIPELINE:
  1. Fetch social platform data (Serper.dev in Tier 3, Scrapling for now)
  2. DeepSeek structures the scraped text into clean lead objects
  3. Filter to recent posts only
  4. No DNS/SMTP/DeepSeek validation gates — social posts have no website to validate
  5. Return leads with platform, profile URL, and intent text

Targets: Reddit, Twitter/X, Facebook groups, LinkedIn posts
"""

import json
import asyncio
from datetime import datetime, timedelta
from typing import List, Dict, Any, Callable, Optional

from scraping.stealth_fetcher import (
    stealth_fetch,
    extract_text_from_html,
    build_github_search_url,
    build_reddit_search_url,
)
from scraping.serper_search import serper_search, build_serper_query_for_social
from ai.deepseek_middleware import execute_llm_payload, DEEPSEEK_SCOUT_MODEL
from dedup.hash_dedup import compute_domain_hash
from config import LEAD_TARGET_FREE, LEAD_TARGET_PAID, SOURCE_TIMEOUT


async def run_social_intent(
    query: str,
    user_tier: str = "free",
    country: str = "",
    state_region: str = "",
    progress_callback: Optional[Callable] = None,
) -> List[Dict[str, Any]]:
    """Find people actively posting about needing help with the query topic."""
    leads = []
    lead_target = LEAD_TARGET_PAID if user_tier != "free" else LEAD_TARGET_FREE

    now = datetime.utcnow()
    one_hour_ago = now - timedelta(hours=1)
    time_str = one_hour_ago.strftime("%Y-%m-%d %H:%M UTC")

    # --------------------------------------------------------
    # PHASE 1: Fetch real data from social platforms (CONCURRENTLY)
    # --------------------------------------------------------
    # PRIMARY: Serper.dev for site:reddit.com OR site:twitter.com social intent queries
    # SUPPLEMENTARY: Scrapling fetch of Reddit search
    # SUPPLEMENTARY: Scrapling fetch of GitHub search
    # All fetched concurrently with asyncio.gather
    if progress_callback:
        await progress_callback(15, "Searching Reddit, Twitter, and GitHub for people asking for help...")

    print(f"[SOCIAL_INTENT] Fetching social intent data for '{query}' (concurrent sources)")

    serper_query = build_serper_query_for_social(query)
    reddit_url = build_reddit_search_url(query)
    github_url = build_github_search_url(query)

    serper_results, reddit_result, github_result = await asyncio.gather(
        serper_search(serper_query, num_results=20),
        stealth_fetch(reddit_url, timeout=SOURCE_TIMEOUT),
        stealth_fetch(github_url, timeout=SOURCE_TIMEOUT),
        return_exceptions=True,
    )

    scraped_texts = []

    # Process Serper.dev results (PRIMARY — replaces Google scraping)
    if isinstance(serper_results, list) and serper_results:
        serper_text = "\n".join(
            f"Title: {r.get('title', '')}\nURL: {r.get('link', '')}\nSnippet: {r.get('snippet', '')}"
            for r in serper_results
        )
        scraped_texts.append({"source": "Google Search (Serper.dev)", "content": serper_text})
        print(f"[SOCIAL_INTENT] Serper.dev returned {len(serper_results)} results")
    elif isinstance(serper_results, Exception):
        print(f"[SOCIAL_INTENT] Serper.dev error: {serper_results}")

    # Process Reddit result
    if isinstance(reddit_result, dict) and reddit_result:
        text = extract_text_from_html(reddit_result["html"])
        if text:
            scraped_texts.append({"source": "Reddit", "content": text})
            print(f"[SOCIAL_INTENT] Scraped Reddit: {len(text)} chars")
    elif isinstance(reddit_result, Exception):
        print(f"[SOCIAL_INTENT] Reddit error: {reddit_result}")

    # Process GitHub result
    if isinstance(github_result, dict) and github_result:
        text = extract_text_from_html(github_result["html"])
        if text:
            scraped_texts.append({"source": "GitHub", "content": text})
            print(f"[SOCIAL_INTENT] Scraped GitHub: {len(text)} chars")
    elif isinstance(github_result, Exception):
        print(f"[SOCIAL_INTENT] GitHub error: {github_result}")

    if not scraped_texts:
        print(f"[SOCIAL_INTENT] All sources failed — no data to process")
        return []

    combined_text = "\n\n".join(
        f"--- SOURCE: {s['source']} ---\n{s['content']}"
        for s in scraped_texts
    )

    # --------------------------------------------------------
    # PHASE 2: DeepSeek — Structure the scraped data
    # --------------------------------------------------------
    if progress_callback:
        await progress_callback(40, "AI is analyzing posts and extracting people with buying intent...")

    print(f"[SOCIAL_INTENT] DeepSeek structuring phase")

    structure_prompt = f"""
    You are a social media intelligence researcher. Below is REAL TEXT scraped from the internet
    about people who are actively seeking help, looking to hire, or expressing buying intent related to: "{query}"

    Your job is to extract REAL people and posts mentioned in this text.
    Do NOT invent or hallucinate people or posts that are not in the text.

    HARD RULES:
    - The person must be actively SEEKING help or expressing NEED
    - We want BUYERS, not sellers
    - Prefer posts from the last hour (after {time_str}) if timestamps are available

    SCRAPED CONTENT:
    {combined_text[:12000]}

    For each REAL person/post you find, provide:
    - name: The person's full name or username as mentioned
    - platform: Which platform they posted on (Reddit, GitHub, Twitter, LinkedIn, etc.)
    - profile_url: Direct link to their profile if mentioned (or "ABSENT")
    - intent_text: The exact text they posted that shows intent

    Return a JSON object with a "people" array. Find up to {lead_target} people.
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

    people = []
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
        parsed = json.loads(content)
        people = parsed.get("people", parsed.get("results", []))
        if isinstance(parsed, list):
            people = parsed

    except Exception as e:
        print(f"[SOCIAL_INTENT] DeepSeek structuring error: {e}")

    print(f"[SOCIAL_INTENT] DeepSeek extracted {len(people)} candidate people")

    # --------------------------------------------------------
    # PHASE 3: Process each person
    # --------------------------------------------------------
    if progress_callback:
        await progress_callback(60, f"Processing {min(len(people), lead_target)} potential leads...")

    for person in people[:lead_target]:
        name = person.get("name", "ABSENT")
        platform = person.get("platform", "ABSENT")
        profile_url = person.get("profile_url", "ABSENT")
        intent_text = person.get("intent_text", "ABSENT")

        if name == "ABSENT" or not name:
            continue

        domain_hash = compute_domain_hash(profile_url if profile_url != "ABSENT" else name)

        # Social intent leads do NOT go through DNS/SMTP/DeepSeek gates
        # — these are real-time social posts, there's no website to validate.
        lead = {
            "domain_hash": domain_hash,
            "company_name": name,
            "website_url": profile_url,
            "dm_name": name,
            "dm_position": "ABSENT",
            "verified_email": "ABSENT",
            "is_catchall": False,
            "linkedin": profile_url if "linkedin" in (profile_url or "").lower() else "ABSENT",
            "instagram": "ABSENT",
            "facebook": "ABSENT",
            "phone": "ABSENT",
            "platform": platform,
            "intent_text": intent_text,
            "validation_gates_passed": 0,
        }

        leads.append(lead)

    print(f"[SOCIAL_INTENT] Returning {len(leads)} leads")
    return leads
