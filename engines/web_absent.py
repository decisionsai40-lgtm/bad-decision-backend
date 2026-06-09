"""
BAD DECISION AI — Engine 3: Web-Absent Aggregators
===================================================
Finds businesses WITHOUT their own website — only on aggregators.
Uses Overpass API (no-website filter) + Scrapling + Yelp/Houzz.
These businesses need a website built — high-value lead for web agencies.

Returns: verified_email, email_source, aggregator_source,
digital_presence_score, opportunity, missing_services
"""

import json
from typing import List, Dict, Any
from scraping.stealth_fetcher import (
    stealth_fetch, extract_text_from_html, extract_emails_from_html,
    build_yelp_search_url, build_houzz_search_url, build_google_search_url,
)
from ai.deepseek_middleware import execute_llm_payload, DEEPSEEK_SCOUT_MODEL
from validation.gate_footprint import check_footprint
from validation.gate_smtp import check_smtp
from dedup.hash_dedup import compute_hash, check_duplicate


async def run_web_absent(query: str, user_tier: str = "free", location: str = "") -> List[Dict[str, Any]]:
    """Find businesses without their own website — only on aggregators."""
    leads = []

    print(f"[WEB_ABSENT] Fetching aggregator data for '{query}' in '{location}'")
    scraped_texts = []

    # Source 1: Overpass API — businesses WITHOUT website tag
    overpass_businesses = []
    try:
        from api_clients.overpass import search_businesses_no_website
        overpass_businesses = await search_businesses_no_website(query, location)
        if overpass_businesses:
            op_text = "\n".join(
                f"Name: {b.get('name', 'N/A')} | Address: {b.get('address', 'N/A')} | "
                f"Phone: {b.get('phone', 'N/A')} | Type: {b.get('type', 'N/A')}"
                for b in overpass_businesses[:30]
            )
            scraped_texts.append({"source": "Overpass API (No Website)", "content": op_text})
            print(f"[WEB_ABSENT] Overpass: {len(overpass_businesses)} businesses without websites")
    except Exception as e:
        print(f"[WEB_ABSENT] Overpass error: {e}")

    # Source 2: Yelp
    yelp_url = build_yelp_search_url(query, location)
    yelp_result = await stealth_fetch(yelp_url)
    if yelp_result:
        text = extract_text_from_html(yelp_result["html"])
        if text:
            scraped_texts.append({"source": "Yelp", "content": text})

    # Source 3: Google search for aggregator-only businesses
    google_url = build_google_search_url(f"{query} site:yelp.com OR site:houzz.com OR site:etsy.com {location}")
    google_result = await stealth_fetch(google_url)
    if google_result:
        text = extract_text_from_html(google_result["html"])
        if text:
            scraped_texts.append({"source": "Google Search", "content": text})

    if not scraped_texts:
        print(f"[WEB_ABSENT] All sources failed")
        return []

    combined_text = "\n\n".join(f"--- SOURCE: {s['source']} ---\n{s['content']}" for s in scraped_texts)

    # PHASE 2: DEEPSEEK
    print(f"[WEB_ABSENT] DeepSeek structuring")
    structure_prompt = f"""
    You are a business data extractor. Below is REAL TEXT about businesses on aggregator platforms related to: "{query}" in "{location}"

    Extract REAL businesses that do NOT have their own standalone website.
    They exist ONLY on aggregator platforms (Yelp, Houzz, Etsy, etc.).

    HARD RULES:
    - Business must NOT have its own website
    - Must exist ONLY on aggregator platform
    - If business has external website, EXCLUDE it

    SCRAPED CONTENT:
    {combined_text[:12000]}

    For each business, provide:
    - company_name: Exact name
    - aggregator_source: Platform they're on (Yelp, Houzz, etc.)
    - aggregator_url: URL to their aggregator profile (or "ABSENT")
    - has_external_website: true/false (MUST be false)
    - phone: Phone number (or "ABSENT")

    Return JSON with "businesses" array. Up to 25.
    """

    try:
        response = await execute_llm_payload({
            "model": DEEPSEEK_SCOUT_MODEL,
            "messages": [
                {"role": "system", "content": "Precise data extractor. Only REAL businesses. Never invent. Valid JSON. 'ABSENT' for missing."},
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
        print(f"[WEB_ABSENT] DeepSeek error: {e}")
        businesses = []

    if overpass_businesses and not businesses:
        businesses = overpass_businesses

    print(f"[WEB_ABSENT] {len(businesses)} candidates")

    # PHASE 3: FILTER & ENRICH
    for biz in businesses[:25]:
        company_name = biz.get("company_name", biz.get("name", "ABSENT"))
        aggregator_source = biz.get("aggregator_source", "OpenStreetMap")
        aggregator_url = biz.get("aggregator_url", "ABSENT")
        has_website = biz.get("has_external_website", False)
        phone = biz.get("phone", "ABSENT")

        if company_name == "ABSENT" or not company_name:
            continue

        if has_website is True or has_website == "true":
            continue

        url_to_hash = aggregator_url if aggregator_url != "ABSENT" else company_name
        domain_hash = compute_hash(url_to_hash)

        is_dup, cached_data = await check_duplicate(domain_hash)
        if is_dup and cached_data:
            leads.append(cached_data)
            continue

        # Enrich with email finding (critical for web-absent leads)
        enrichment = await _enrich_aggregator_lead(company_name, aggregator_source, aggregator_url, phone, location)

        # Calculate digital presence score (0-100)
        dp_score = 10  # Base: they're on at least one aggregator
        if aggregator_url != "ABSENT":
            dp_score += 15
        if enrichment.get("verified_email", "ABSENT") != "ABSENT":
            dp_score += 20
        if enrichment.get("phone", "ABSENT") != "ABSENT":
            dp_score += 15
        dp_score = min(dp_score, 100)

        lead = {
            "domain_hash": domain_hash,
            "company_name": company_name,
            "website_url": aggregator_url,
            "dm_name": enrichment.get("dm_name", "ABSENT"),
            "dm_position": enrichment.get("dm_position", "ABSENT"),
            "verified_email": enrichment.get("verified_email", "ABSENT"),
            "email_source": enrichment.get("email_source", "ABSENT"),
            "is_catchall": False,
            "phone": enrichment.get("phone", phone),
            "aggregator_source": aggregator_source,
            "aggregator_url": aggregator_url,
            "digital_presence_score": dp_score,
            "opportunity": enrichment.get("opportunity", "Needs a professional website"),
            "missing_services": enrichment.get("missing_services", "Website, SEO, Online presence"),
        }

        # Gate 2: Footprint (Starter+)
        if user_tier in ("starter", "growth", "pro"):
            if not check_footprint(lead):
                continue

        # Gate 3: SMTP (Pro only)
        if user_tier == "pro" and lead["verified_email"] != "ABSENT":
            smtp_ok, is_catchall = await check_smtp(lead["verified_email"])
            lead["is_catchall"] = is_catchall
            if not smtp_ok and not is_catchall:
                continue

        leads.append(lead)

    return leads


async def _enrich_aggregator_lead(
    company_name: str, aggregator_source: str, aggregator_url: str, phone: str, location: str
) -> Dict[str, str]:
    """Enrich an aggregator-listed business with contact details and opportunity analysis."""
    scraped_profile_text = ""
    scraped_emails = []

    if aggregator_url and aggregator_url != "ABSENT":
        profile_result = await stealth_fetch(aggregator_url)
        if profile_result:
            scraped_profile_text = extract_text_from_html(profile_result["html"], max_chars=8000)
            scraped_emails = extract_emails_from_html(profile_result["html"])

    # Try Hunter.io for domain-less businesses (use company name search)
    hunter_email = ""
    try:
        from api_clients.hunter_client import find_email_by_name
        hunter_email, _ = await find_email_by_name(company_name, location)
    except Exception:
        pass

    email_hint = ""
    if scraped_emails:
        email_hint = f"\nEMAILS FOUND: {', '.join(scraped_emails[:5])}"
    if hunter_email:
        email_hint += f"\nHUNTER.IO: {hunter_email}"

    if scraped_profile_text:
        prompt = f"""
        Extract info for "{company_name}" on {aggregator_source} at {aggregator_url}:
        {email_hint}

        SCRAPED CONTENT:
        {scraped_profile_text[:8000]}

        Return JSON:
        - dm_name: Owner/manager name (or "ABSENT")
        - dm_position: Their title (or "ABSENT")
        - verified_email: Email (or "ABSENT")
        - email_source: How email was found (or "ABSENT")
        - phone: Phone number (or "ABSENT")
        - opportunity: What services this business needs (e.g., "Professional website, online booking, SEO")
        - missing_services: Comma-separated list of missing digital services

        Use "ABSENT" for missing fields.
        """
    else:
        prompt = f"""
        Find contact details for: "{company_name}" on {aggregator_source} in {location}
        {email_hint}

        Return JSON: dm_name, dm_position, verified_email, email_source, phone, opportunity, missing_services.
        Use "ABSENT" for missing.
        """

    try:
        response = await execute_llm_payload({
            "model": DEEPSEEK_SCOUT_MODEL,
            "messages": [
                {"role": "system", "content": "Precise data extractor. Only from text. Never invent. Valid JSON. 'ABSENT' for missing."},
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        })
        content = response.get("choices", [{}])[0].get("message", {}).get("content", "{}")
        return json.loads(content)
    except Exception as e:
        print(f"[WEB_ABSENT] Enrichment error: {e}")
        result = {
            "dm_name": "ABSENT", "dm_position": "ABSENT", "verified_email": "ABSENT",
            "email_source": "ABSENT", "phone": phone or "ABSENT",
            "opportunity": "Needs a professional website", "missing_services": "Website, SEO, Online presence",
        }
        if hunter_email:
            result["verified_email"] = hunter_email
            result["email_source"] = "hunter_io"
        elif scraped_emails:
            result["verified_email"] = scraped_emails[0]
            result["email_source"] = "aggregator_profile_regex"
        return result
