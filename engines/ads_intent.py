"""
BAD DECISION AI — Engine 1: Digital Ads Intelligence
=====================================================
Finds businesses actively running ads. Uses Meta Ads Library
and Serper.dev for discovery, Scrapling for website scraping,
DeepSeek for structuring, and email enrichment pipeline.

Returns engine-specific schema with ad_platform, ad_spend_signal.
"""

import json
from typing import List, Dict, Any
from scraping.stealth_fetcher import (
    stealth_fetch, extract_text_from_html, extract_emails_from_html,
    build_meta_ads_library_url, build_google_search_url,
)
from ai.deepseek_middleware import execute_llm_payload, DEEPSEEK_SCOUT_MODEL
from validation.gate_dns import check_dns
from validation.gate_footprint import check_footprint
from validation.gate_smtp import check_smtp
from dedup.hash_dedup import compute_hash, check_duplicate


async def run_ads_intent(query: str, user_tier: str = "free", location: str = "") -> List[Dict[str, Any]]:
    """Find businesses running ads related to the query."""
    leads = []

    # PHASE 1: SCRAPLING — Fetch real data
    print(f"[ADS_INTENT] Fetching ad data for '{query}'")
    scraped_texts = []

    # Source 1: Meta Ads Library
    meta_url = build_meta_ads_library_url(query)
    meta_result = await stealth_fetch(meta_url)
    if meta_result:
        text = extract_text_from_html(meta_result["html"])
        if text:
            scraped_texts.append({"source": "Meta Ads Library", "content": text})
            print(f"[ADS_INTENT] Meta Ads: {len(text)} chars")

    # Source 2: Serper.dev (if API key available)
    try:
        from api_clients.serper import serper_search
        serper_results = await serper_search(f"{query} advertising ads running", num_results=15)
        if serper_results:
            serper_text = "\n".join(
                f"Title: {r.get('title', '')}\nSnippet: {r.get('snippet', '')}\nURL: {r.get('link', '')}"
                for r in serper_results
            )
            scraped_texts.append({"source": "Serper.dev", "content": serper_text})
            print(f"[ADS_INTENT] Serper: {len(serper_results)} results")
    except Exception as e:
        print(f"[ADS_INTENT] Serper fallback: {e}")

    # Source 3: Google search fallback
    if len(scraped_texts) < 2:
        google_url = build_google_search_url(f"{query} running ads advertising")
        google_result = await stealth_fetch(google_url)
        if google_result:
            text = extract_text_from_html(google_result["html"])
            if text:
                scraped_texts.append({"source": "Google Search", "content": text})
                print(f"[ADS_INTENT] Google: {len(text)} chars")

    if not scraped_texts:
        print(f"[ADS_INTENT] All sources failed")
        return []

    combined_text = "\n\n".join(f"--- SOURCE: {s['source']} ---\n{s['content']}" for s in scraped_texts)

    # PHASE 2: DEEPSEEK — Structure the data
    print(f"[ADS_INTENT] DeepSeek structuring")
    structure_prompt = f"""
    You are a business data extractor. Below is REAL TEXT scraped from the internet
    about businesses running ads related to: "{query}"

    Extract REAL businesses mentioned in this text. Do NOT invent businesses.
    Only extract companies clearly named in the scraped content.

    SCRAPED CONTENT:
    {combined_text[:12000]}

    For each REAL business, provide:
    - company_name: The exact business name
    - website_url: Their website domain (or "ABSENT")
    - ad_platform: Which platform they advertise on (or "ABSENT")

    Return a JSON object with a "businesses" array. Find up to 25 businesses.
    """

    try:
        response = await execute_llm_payload({
            "model": DEEPSEEK_SCOUT_MODEL,
            "messages": [
                {"role": "system", "content": "You are a precise data extractor. Only extract REAL businesses. Never invent data. Always respond with valid JSON. Use 'ABSENT' for missing data."},
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
        print(f"[ADS_INTENT] DeepSeek error: {e}")
        businesses = []

    print(f"[ADS_INTENT] DeepSeek found {len(businesses)} candidates")

    # PHASE 3: VALIDATE & ENRICH
    for biz in businesses[:25]:
        company_name = biz.get("company_name", "ABSENT")
        website_url = biz.get("website_url", "ABSENT")
        ad_platform = biz.get("ad_platform", "ABSENT")

        if company_name == "ABSENT" or not company_name:
            continue

        url_to_hash = website_url if website_url != "ABSENT" else company_name
        domain_hash = compute_hash(url_to_hash)

        is_dup, cached_data = await check_duplicate(domain_hash)
        if is_dup and cached_data:
            leads.append(cached_data)
            continue

        # Gate 1: DNS
        if website_url != "ABSENT":
            dns_ok = await check_dns(website_url)
            if not dns_ok:
                print(f"[ADS_INTENT] DNS failed for {website_url} — DROPPED")
                continue

        # Enrich
        enrichment = await _enrich_lead(company_name, website_url, user_tier)

        lead = {
            "domain_hash": domain_hash,
            "company_name": company_name,
            "website_url": website_url,
            "dm_name": enrichment.get("dm_name", "ABSENT"),
            "dm_position": enrichment.get("dm_position", "ABSENT"),
            "verified_email": enrichment.get("verified_email", "ABSENT"),
            "email_source": enrichment.get("email_source", "ABSENT"),
            "is_catchall": False,
            "linkedin": enrichment.get("linkedin", "ABSENT"),
            "instagram": enrichment.get("instagram", "ABSENT"),
            "phone": enrichment.get("phone", "ABSENT"),
            "ad_platform": ad_platform,
        }

        # Gate 2: Footprint (Starter+)
        if user_tier in ("starter", "growth", "pro"):
            footprint_ok = check_footprint(lead)
            if not footprint_ok:
                continue

        # Gate 3: SMTP (Pro only)
        if user_tier == "pro" and lead["verified_email"] != "ABSENT":
            smtp_ok, is_catchall = await check_smtp(lead["verified_email"])
            lead["is_catchall"] = is_catchall
            if not smtp_ok and not is_catchall:
                continue

        leads.append(lead)

    return leads


async def _enrich_lead(company_name: str, website_url: str, user_tier: str) -> Dict[str, str]:
    """Enrich a lead with decision maker details and email."""
    scraped_website_text = ""
    scraped_emails = []

    if website_url and website_url != "ABSENT":
        print(f"[ADS_INTENT] Scraping {website_url}")
        site_result = await stealth_fetch(website_url)
        if site_result:
            scraped_website_text = extract_text_from_html(site_result["html"], max_chars=8000)
            scraped_emails = extract_emails_from_html(site_result["html"])
            print(f"[ADS_INTENT] Scraped site: {len(scraped_website_text)} chars, {len(scraped_emails)} emails")

    # Email enrichment: try Hunter.io
    hunter_email = ""
    try:
        from api_clients.hunter_client import find_email
        domain = website_url.replace("https://", "").replace("http://", "").split("/")[0] if website_url != "ABSENT" else ""
        if domain:
            hunter_email, email_source = await find_email(domain, company_name)
    except Exception:
        pass

    # Build prompt
    email_hint = ""
    if scraped_emails:
        email_hint = f"\n\nEMAILS FOUND ON WEBSITE: {', '.join(scraped_emails[:5])}"
    if hunter_email:
        email_hint += f"\nHUNTER.IO FOUND: {hunter_email}"

    if scraped_website_text:
        prompt = f"""
        Extract contact info from this scraped website of "{company_name}" ({website_url}):
        {email_hint}

        SCRAPED CONTENT:
        {scraped_website_text[:8000]}

        Return JSON: dm_name, dm_position, verified_email, email_source (how you found the email: "website_contact_page", "hunter_io", "website_regex", etc.), linkedin, instagram, phone.
        Use "ABSENT" for missing fields.
        """
    else:
        prompt = f"""
        Find the key decision maker for: "{company_name}" ({website_url})
        {email_hint}
        Return JSON: dm_name, dm_position, verified_email, email_source, linkedin, instagram, phone.
        Use "ABSENT" for missing fields.
        """

    try:
        response = await execute_llm_payload({
            "model": DEEPSEEK_SCOUT_MODEL,
            "messages": [
                {"role": "system", "content": "You are a precise data extractor. Only extract from provided text. Never invent. Always respond with valid JSON. Use 'ABSENT' for missing."},
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        })
        content = response.get("choices", [{}])[0].get("message", {}).get("content", "{}")
        return json.loads(content)
    except Exception as e:
        print(f"[ADS_INTENT] Enrichment error: {e}")
        result = {
            "dm_name": "ABSENT", "dm_position": "ABSENT", "verified_email": "ABSENT",
            "email_source": "ABSENT", "linkedin": "ABSENT", "instagram": "ABSENT", "phone": "ABSENT",
        }
        if hunter_email:
            result["verified_email"] = hunter_email
            result["email_source"] = "hunter_io"
        elif scraped_emails:
            result["verified_email"] = scraped_emails[0]
            result["email_source"] = "website_regex"
        return result
