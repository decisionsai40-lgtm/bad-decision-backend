"""
BAD DECISION — Engine 4: social_intent (Social Radar)
=====================================================
This engine finds people who are actively asking for help or
expressing buying intent on social platforms (Reddit, Twitter,
Facebook, LinkedIn).

PIPELINE:
  1. 10x concurrent Serper web searches (build_social_intent_queries —
     targets site:reddit.com, site:twitter.com, etc.)
  2. ScrapingAnt fetches any Reddit URLs found (JS rendering)
  3. DeepSeek structures the text, extracting people + their posts
  4. NO validation gates (social posts have no website to validate)
  5. Infer intent_level from the post text:
       High   = "looking for", "need help", "hiring"
       Medium = "considering", "thinking about"
       Low    = everything else

UNIQUE FIELDS:
  - platform         (string — "Reddit", "Twitter", "Facebook", "LinkedIn")
  - intent_text      (string — the actual post text)
  - post_url         (string — link to the post)
  - intent_level     (string — "High", "Medium", "Low")
  - author_username  (string — if available)
"""

import json
import asyncio
from typing import List, Dict, Any, Callable, Optional

from scraping.serper_search import serper_search, build_social_intent_queries
from scraping.scrapingant import scrape_with_js
from scraping.stealth_fetcher import build_reddit_search_url
from scraping.email_scraper import enrich_lead_with_email
from ai.deepseek_middleware import execute_llm_payload, DEEPSEEK_SCOUT_MODEL
from dedup.hash_dedup import compute_domain_hash
from config import SCRAPINGANT_API_KEY


# Intent inference keywords (case-insensitive substring match)
INTENT_HIGH_KEYWORDS = [
    "looking for", "need help", "hiring", "need a", "need someone",
    "searching for", "seeking", "any recommendations", "can anyone recommend",
    "who does", "where can i find", "how do i find", "i need to hire",
    "looking to hire", "recommend a", "need recommendation",
]
INTENT_MEDIUM_KEYWORDS = [
    "considering", "thinking about", "might need", "maybe",
    "exploring options", "weighing options", "looking into",
    "in the market for", "researching", "comparing",
]


# ============================================================
# MAIN ENTRY POINT
# ============================================================
async def run_social_intent(
    query: str,
    user_tier: str = "free",
    country: str = "",
    state_region: str = "",
    lead_target: int = 50,
    progress_callback: Optional[Callable] = None,
) -> List[Dict[str, Any]]:
    """Find people actively posting about needing help with the query topic."""
    leads: List[Dict[str, Any]] = []
    seen_hashes: set = set()

    location_parts = [p for p in [state_region, country] if p]
    location = ", ".join(location_parts) if location_parts else ""

    print(f"[SOCIAL_INTENT] Start — query='{query}', tier={user_tier}, target={lead_target}, loc='{location}'")

    # --------------------------------------------------------
    # PHASE 1: 10x Serper web searches + Reddit via ScrapingAnt (CONCURRENT)
    # --------------------------------------------------------
    if progress_callback:
        await progress_callback(15, "Searching Reddit, Twitter, Facebook, and LinkedIn for buying intent posts...")

    web_queries = build_social_intent_queries(query, location)
    web_tasks = [serper_search(q, num_results=10) for q in web_queries]

    # Optionally fetch Reddit search page via ScrapingAnt (JS rendering)
    reddit_task: Optional[Any] = None
    if SCRAPINGANT_API_KEY:
        reddit_task = scrape_with_js(build_reddit_search_url(query))
    else:
        print("[SOCIAL_INTENT] ScrapingAnt not configured — skipping Reddit JS fetch")

    tasks_to_run: List[Any] = list(web_tasks)
    if reddit_task is not None:
        tasks_to_run.append(reddit_task)

    all_fetches = await asyncio.gather(*tasks_to_run, return_exceptions=True)

    web_results_list = all_fetches[:len(web_tasks)]
    reddit_html = all_fetches[len(web_tasks)] if reddit_task is not None else None

    # --- Process Serper web results (dedup by URL) ---
    all_web_results: List[Dict[str, Any]] = []
    seen_urls: set = set()
    reddit_profile_urls: List[str] = []
    for i, r in enumerate(web_results_list):
        if isinstance(r, Exception):
            print(f"[SOCIAL_INTENT] Serper web search {i+1} error: {r}")
            continue
        if not isinstance(r, list):
            continue
        for item in r:
            url = item.get("link", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_web_results.append(item)
                if "reddit.com" in url.lower():
                    reddit_profile_urls.append(url)

    print(f"[SOCIAL_INTENT] Serper web: {len(all_web_results)} unique results across 10 queries "
          f"(Reddit URLs: {len(reddit_profile_urls)})")

    # --- Process Reddit ScrapingAnt result ---
    scraped_texts: List[Dict[str, str]] = []
    if isinstance(reddit_html, str) and reddit_html:
        scraped_texts.append({
            "source": "Reddit search (ScrapingAnt)",
            "content": reddit_html[:8000],
        })
        print(f"[SOCIAL_INTENT] Scraped Reddit via ScrapingAnt: {len(reddit_html)} chars")
    elif isinstance(reddit_html, Exception):
        print(f"[SOCIAL_INTENT] Reddit ScrapingAnt error: {reddit_html}")

    # Format Serper web results as text for DeepSeek
    if all_web_results:
        serper_text = "\n\n".join(
            f"Title: {r.get('title', '')}\nURL: {r.get('link', '')}\nSnippet: {r.get('snippet', '')}"
            for r in all_web_results
        )
        scraped_texts.append({"source": "Google Search (Serper.dev)", "content": serper_text})

    # --------------------------------------------------------
    # PHASE 1b: OPTIONAL — ScrapingAnt deep-fetch on Reddit post URLs
    # --------------------------------------------------------
    if SCRAPINGANT_API_KEY and reddit_profile_urls and len(leads) < lead_target:
        deep_fetch_urls = reddit_profile_urls[:3]
        if progress_callback:
            await progress_callback(25, f"Rendering {len(deep_fetch_urls)} Reddit posts with ScrapingAnt...")

        print(f"[SOCIAL_INTENT] ScrapingAnt deep-fetch on {len(deep_fetch_urls)} Reddit URLs")
        deep_tasks = [scrape_with_js(u) for u in deep_fetch_urls]
        deep_htmls = await asyncio.gather(*deep_tasks, return_exceptions=True)

        for i, html in enumerate(deep_htmls):
            if isinstance(html, str) and html:
                scraped_texts.append({
                    "source": f"Reddit post (ScrapingAnt) — {deep_fetch_urls[i]}",
                    "content": html[:6000],
                })
                print(f"[SOCIAL_INTENT] Scraped Reddit post via ScrapingAnt: {len(html)} chars")
            elif isinstance(html, Exception):
                print(f"[SOCIAL_INTENT] Reddit post ScrapingAnt error: {html}")

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
    about people who are actively seeking help, looking to hire, or expressing buying intent
    related to: "{query}" in "{location or 'unspecified location'}".

    Extract REAL people and posts mentioned in this text. Do NOT invent people or posts.
    Be aggressive — extract every genuine intent-post you can find.

    HARD RULES:
    - The person must be actively SEEKING help or expressing NEED
    - We want BUYERS, not sellers
    - Skip posts that are obviously promotional/advertising

    SCRAPED CONTENT:
    {combined_text[:12000]}

    For each REAL person/post you find, provide:
    - name: The person's full name OR username as mentioned (or "ABSENT")
    - author_username: Their social media handle/username if known (or "ABSENT")
    - platform: Which platform they posted on — "Reddit", "Twitter", "Facebook",
      "LinkedIn", "Nextdoor", or "Unknown"
    - post_url: Direct link to the post if mentioned (or "ABSENT")
    - intent_text: The exact text they posted that shows intent (verbatim, or "ABSENT")

    Return a JSON object with a "people" array. Find up to {lead_target} people.
    If you cannot find data for a field, write "ABSENT".

    Example:
    {{
        "people": [
            {{
                "name": "John Smith",
                "author_username": "jsmith1985",
                "platform": "Reddit",
                "post_url": "https://reddit.com/r/roofing/comments/abc/looking_for_roofer",
                "intent_text": "Looking for a reliable roofing contractor in Dallas. Any recommendations?"
            }}
        ]
    }}
    """

    people: List[Dict[str, Any]] = []
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
    # PHASE 3: Process each person — infer intent_level, build lead
    # --------------------------------------------------------
    if progress_callback:
        await progress_callback(60, f"Processing {min(len(people), lead_target)} potential leads...")

    for person in people[:lead_target]:
        if len(leads) >= lead_target:
            break

        name = (person.get("name") or "").strip()
        if not name or name == "ABSENT":
            # Fall back to author_username if name is missing
            name = (person.get("author_username") or "").strip()
            if not name or name == "ABSENT":
                continue

        platform = (person.get("platform") or "Unknown").strip() or "Unknown"
        post_url = person.get("post_url", "ABSENT")
        author_username = person.get("author_username", "ABSENT")
        intent_text = person.get("intent_text", "ABSENT")

        # Infer intent_level from the post text
        intent_level = _infer_intent_level(intent_text)

        # Dedup by post_url first (falls back to name)
        dedup_key = post_url if (post_url and post_url != "ABSENT") else name
        domain_hash = compute_domain_hash(dedup_key)
        if domain_hash in seen_hashes:
            continue
        seen_hashes.add(domain_hash)

        # Determine LinkedIn URL if platform is LinkedIn
        linkedin_url = post_url if (platform.lower() == "linkedin" and post_url != "ABSENT") else "ABSENT"

        # Email scraper enrichment — even social posts sometimes link to personal
        # sites/profiles where an email can be scraped. The function gracefully
        # returns ABSENT when there's nothing to find.
        try:
            enrichment = await enrich_lead_with_email(name, post_url if post_url != "ABSENT" else "ABSENT")
        except Exception as e:
            print(f"[SOCIAL_INTENT] Email scraper error for {name}: {e}")
            enrichment = {
                "verified_email": "ABSENT", "phone": "ABSENT",
                "facebook": "ABSENT", "instagram": "ABSENT", "linkedin": "ABSENT",
            }

        # If email scraper found a LinkedIn URL and we don't already have one, use it
        if linkedin_url == "ABSENT" and enrichment.get("linkedin", "ABSENT") != "ABSENT":
            linkedin_url = enrichment.get("linkedin")

        # Social intent leads do NOT go through DNS/SMTP/DeepSeek gates —
        # these are real-time social posts, there's no website to validate.
        lead = {
            "domain_hash": domain_hash,
            "company_name": name,  # Use the person's name as the lead identifier
            "website_url": post_url if post_url != "ABSENT" else "ABSENT",
            "dm_name": name,
            "dm_position": "ABSENT",
            "verified_email": enrichment.get("verified_email", "ABSENT"),
            "is_catchall": False,
            "linkedin": linkedin_url,
            "instagram": enrichment.get("instagram", "ABSENT"),
            "facebook": enrichment.get("facebook", "ABSENT"),
            "phone": enrichment.get("phone", "ABSENT"),
            "platform": platform,
            "intent_text": intent_text,
            "post_url": post_url,
            "intent_level": intent_level,
            "author_username": author_username,
            "validation_gates_passed": 0,
        }

        leads.append(lead)

    if progress_callback:
        await progress_callback(90, f"Found {len(leads)} social intent leads")

    print(f"[SOCIAL_INTENT] Returning {len(leads)} leads")
    return leads


# ============================================================
# HELPERS
# ============================================================
def _infer_intent_level(text: str) -> str:
    """
    Infer the intent level from the post text.

    High   = explicit buying/hiring intent ("looking for", "need help", "hiring")
    Medium = soft intent ("considering", "thinking about")
    Low    = everything else
    """
    if not text or text == "ABSENT":
        return "Low"

    text_lower = text.lower()

    for kw in INTENT_HIGH_KEYWORDS:
        if kw in text_lower:
            return "High"

    for kw in INTENT_MEDIUM_KEYWORDS:
        if kw in text_lower:
            return "Medium"

    return "Low"
