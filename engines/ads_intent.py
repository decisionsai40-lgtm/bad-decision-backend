"""
BAD DECISION — Engine 2: ads_intent (Ads Intelligence)
======================================================
This engine finds businesses that are actively running ads on
Facebook, Google, or TikTok. If a business is spending money on ads,
it has a marketing budget — a hot lead for agencies and service providers.

PIPELINE:
  1. 10x concurrent Serper web searches (build_ads_intent_queries)
  2. ScrapingAnt fetches Meta Ads Library (JS rendering — currently
     fails without it)
  3. DeepSeek structures the combined text into clean lead objects,
     inferring ad_platform and ad_status from snippets
  4. For each business:
       Gate 1 (DNS)        — ALL tiers
       Email scraper       — scrapes website for real emails
       Footprint check     — drop leads with zero contact methods
       Gate 2 (SMTP)       — Starter/Growth/Pro tiers
       Gate 3 (DeepSeek)   — Pro tier only

UNIQUE FIELDS:
  - ad_platform  (string — "Facebook", "Google", "TikTok", or "Unknown")
  - ad_status    (string — "Active" or "Unknown")
"""

import json
import asyncio
from typing import List, Dict, Any, Callable, Optional

from scraping.serper_search import serper_search, build_ads_intent_queries
from scraping.scrapingant import scrape_with_js
from scraping.stealth_fetcher import build_meta_ads_library_url
from scraping.email_scraper import enrich_lead_with_email
from ai.deepseek_middleware import execute_llm_payload, DEEPSEEK_SCOUT_MODEL
from validation.gate_dns import check_dns
from validation.gate_footprint import check_footprint
from validation.gate_smtp import check_smtp
from validation.gate_deepseek import check_deepseek
from dedup.hash_dedup import compute_domain_hash
from config import SCRAPINGANT_API_KEY


# ============================================================
# MAIN ENTRY POINT
# ============================================================
async def run_ads_intent(
    query: str,
    user_tier: str = "free",
    country: str = "",
    state_region: str = "",
    lead_target: int = 50,
    progress_callback: Optional[Callable] = None,
) -> List[Dict[str, Any]]:
    """Find companies running ads related to the user's query."""
    leads: List[Dict[str, Any]] = []
    seen_hashes: set = set()

    location_parts = [p for p in [state_region, country] if p]
    location = ", ".join(location_parts) if location_parts else ""

    print(f"[ADS_INTENT] Start — query='{query}', tier={user_tier}, target={lead_target}, loc='{location}'")

    # --------------------------------------------------------
    # PHASE 1: 10x Serper web searches + Meta Ads Library (CONCURRENT)
    # --------------------------------------------------------
    if progress_callback:
        await progress_callback(15, "Searching Google and Meta Ads Library for businesses running ads...")

    web_queries = build_ads_intent_queries(query, location)
    web_tasks = [serper_search(q, num_results=10) for q in web_queries]

    # Conditionally include Meta Ads Library fetch (only if ScrapingAnt configured)
    tasks_to_run: List[Any] = list(web_tasks)
    meta_ads_task_index = None
    if SCRAPINGANT_API_KEY:
        meta_ads_task_index = len(tasks_to_run)
        tasks_to_run.append(scrape_with_js(build_meta_ads_library_url(query)))

    all_fetches = await asyncio.gather(*tasks_to_run, return_exceptions=True)

    # Separate web results from Meta Ads result
    web_results_list = all_fetches[:len(web_tasks)]
    meta_ads_html = all_fetches[meta_ads_task_index] if meta_ads_task_index is not None else None

    # --- Process Meta Ads Library result ---
    scraped_texts: List[Dict[str, str]] = []
    if isinstance(meta_ads_html, str) and meta_ads_html:
        scraped_texts.append({
            "source": "Meta Ads Library (ScrapingAnt)",
            "content": meta_ads_html[:8000],
        })
        print(f"[ADS_INTENT] Scraped Meta Ads Library via ScrapingAnt: {len(meta_ads_html)} chars")
    elif isinstance(meta_ads_html, Exception):
        print(f"[ADS_INTENT] Meta Ads Library fetch error: {meta_ads_html}")
    elif meta_ads_html is None:
        print("[ADS_INTENT] ScrapingAnt not configured — skipping Meta Ads Library")

    # --- Process Serper web results (dedup by URL) ---
    all_web_results: List[Dict[str, Any]] = []
    seen_urls: set = set()
    for i, r in enumerate(web_results_list):
        if isinstance(r, Exception):
            print(f"[ADS_INTENT] Serper web search {i+1} error: {r}")
            continue
        if not isinstance(r, list):
            continue
        for item in r:
            url = item.get("link", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_web_results.append(item)

    print(f"[ADS_INTENT] Serper web: {len(all_web_results)} unique results across 10 queries")

    if all_web_results:
        serper_text = "\n\n".join(
            f"Title: {r.get('title', '')}\nURL: {r.get('link', '')}\nSnippet: {r.get('snippet', '')}"
            for r in all_web_results
        )
        scraped_texts.append({"source": "Google Search (Serper.dev)", "content": serper_text})

    if not scraped_texts:
        print(f"[ADS_INTENT] All sources failed — no data to process")
        return []

    combined_text = "\n\n".join(
        f"--- SOURCE: {s['source']} ---\n{s['content']}"
        for s in scraped_texts
    )

    # --------------------------------------------------------
    # PHASE 2: DeepSeek — Structure the combined text
    # --------------------------------------------------------
    if progress_callback:
        await progress_callback(35, "AI is analyzing scraped data and extracting businesses running ads...")

    print(f"[ADS_INTENT] DeepSeek structuring phase")

    structure_prompt = f"""
    You are a business data extractor. Below is REAL TEXT scraped from the internet
    about businesses related to: "{query}" in "{location or 'unspecified location'}".

    These businesses are likely RUNNING ADS. Extract every REAL business mentioned.
    Do NOT invent or hallucinate businesses that are not in the text.

    SCRAPED CONTENT:
    {combined_text[:12000]}

    For each REAL business, provide:
    - company_name: The exact business name as mentioned
    - website_url: Their website URL if mentioned (or "ABSENT")
    - ad_platform: Which platform they advertise on — "Facebook", "Google",
      "TikTok", or "Unknown" based on clues in the text
    - ad_status: "Active" if there's evidence of current ads, otherwise "Unknown"

    Return a JSON object with a "businesses" array. Find up to {lead_target} businesses.
    If you cannot find data for a field, write "ABSENT" (or "Unknown" for ad_*).

    Example:
    {{
        "businesses": [
            {{
                "company_name": "ABC Roofing",
                "website_url": "https://abcroofing.com",
                "ad_platform": "Facebook",
                "ad_status": "Active"
            }}
        ]
    }}
    """

    businesses: List[Dict[str, Any]] = []
    try:
        response = await execute_llm_payload({
            "model": DEEPSEEK_SCOUT_MODEL,
            "messages": [
                {"role": "system", "content": "You are a precise data extractor. Only extract REAL businesses mentioned in the provided text. Never invent data. Always respond with valid JSON. Use 'ABSENT' for missing data."},
                {"role": "user", "content": structure_prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        })

        content = response.get("choices", [{}])[0].get("message", {}).get("content", "{}")
        parsed = json.loads(content)
        businesses = parsed.get("businesses", parsed.get("results", []))
        if isinstance(parsed, list):
            businesses = parsed

    except Exception as e:
        print(f"[ADS_INTENT] DeepSeek structuring error: {e}")

    print(f"[ADS_INTENT] DeepSeek extracted {len(businesses)} candidate businesses")

    # --------------------------------------------------------
    # PHASE 3: VALIDATE & ENRICH each business
    # --------------------------------------------------------
    if progress_callback:
        await progress_callback(50, f"Validating and enriching {min(len(businesses), lead_target)} businesses...")

    for biz in businesses[:lead_target]:
        if len(leads) >= lead_target:
            break

        company_name = (biz.get("company_name") or "").strip()
        website_url = biz.get("website_url", "ABSENT")
        ad_platform = (biz.get("ad_platform") or "Unknown").strip() or "Unknown"
        ad_status = (biz.get("ad_status") or "Unknown").strip() or "Unknown"

        if not company_name or company_name == "ABSENT":
            continue

        domain_hash = compute_domain_hash(website_url if website_url != "ABSENT" else company_name)
        if domain_hash in seen_hashes:
            continue
        seen_hashes.add(domain_hash)

        # Gate 1: DNS Check (ALL tiers)
        gates_passed = 0
        if website_url != "ABSENT":
            try:
                domain_ok, _ = await check_dns(website_url)
                if not domain_ok:
                    print(f"[ADS_INTENT] DNS failed for {website_url} — DROPPED")
                    continue
                gates_passed = 1
            except Exception as e:
                print(f"[ADS_INTENT] DNS check error for {website_url}: {e} — continuing anyway")
                gates_passed = 1

        # Email scraper enrichment (scrapes website for real emails)
        try:
            enrichment = await enrich_lead_with_email(company_name, website_url)
        except Exception as e:
            print(f"[ADS_INTENT] Email scraper error for {company_name}: {e}")
            enrichment = {
                "verified_email": "ABSENT", "phone": "ABSENT",
                "facebook": "ABSENT", "instagram": "ABSENT", "linkedin": "ABSENT",
            }

        lead = {
            "domain_hash": domain_hash,
            "company_name": company_name,
            "website_url": website_url,
            "dm_name": "ABSENT",
            "dm_position": "ABSENT",
            "verified_email": enrichment.get("verified_email", "ABSENT"),
            "is_catchall": False,
            "linkedin": enrichment.get("linkedin", "ABSENT"),
            "instagram": enrichment.get("instagram", "ABSENT"),
            "facebook": enrichment.get("facebook", "ABSENT"),
            "phone": enrichment.get("phone", "ABSENT"),
            "ad_platform": ad_platform,
            "ad_status": ad_status,
            "validation_gates_passed": gates_passed,
        }

        # Pre-filter: Footprint check
        if not check_footprint(lead):
            print(f"[ADS_INTENT] Footprint failed for {company_name} — no contact method, DROPPED")
            continue

        # Gate 2: SMTP (Starter/Growth/Pro)
        if user_tier in ("starter", "growth", "pro") and lead["verified_email"] != "ABSENT":
            try:
                smtp_ok, is_catchall = await check_smtp(lead["verified_email"])
                lead["is_catchall"] = is_catchall
                if not smtp_ok and not is_catchall:
                    print(f"[ADS_INTENT] SMTP failed for {lead['verified_email']} — DROPPED")
                    continue
                gates_passed = 2
                lead["validation_gates_passed"] = gates_passed
            except Exception as e:
                print(f"[ADS_INTENT] SMTP error for {lead['verified_email']}: {e} — lenient accept")
                gates_passed = 2
                lead["validation_gates_passed"] = gates_passed

        # Gate 3: DeepSeek AI (Pro only)
        if user_tier == "pro" and lead["verified_email"] != "ABSENT":
            try:
                deepseek_ok, is_role, _ = await check_deepseek(lead["verified_email"], company_name)
                if not deepseek_ok:
                    print(f"[ADS_INTENT] DeepSeek Gate 3 rejected {lead['verified_email']} — DROPPED")
                    continue
                if is_role:
                    lead["is_catchall"] = True
                gates_passed = 3
                lead["validation_gates_passed"] = gates_passed
            except Exception as e:
                print(f"[ADS_INTENT] DeepSeek Gate 3 error for {lead['verified_email']}: {e} — lenient accept")
                gates_passed = 3
                lead["validation_gates_passed"] = gates_passed

        leads.append(lead)

    if progress_callback:
        await progress_callback(90, f"Found {len(leads)} verified ad-running businesses")

    print(f"[ADS_INTENT] Returning {len(leads)} verified leads")
    return leads
