"""
BAD DECISION AI — Complete Backend (Single File)
=================================================
This file contains EVERYTHING the backend needs:
- FastAPI web server
- 4 Search Engines (ads_intent, smb_maps, web_absent, social_intent)
- 3-Gate Validation (DNS, Footprint, SMTP)
- DeepSeek AI Middleware (multi-key ring)
- SHA-256 Hash Dedup + Global Cache
- Background Task Worker
- Scrapling Fetcher (curl_cffi TLS impersonation, NO browser needed)

Combined into one file so it's easy to deploy on GitHub + Render.
"""

import os
import re
import json
import hashlib
import smtplib
import random
import string
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Any, List, Tuple, Optional
from urllib.parse import urlparse

import dns.resolver
import httpx
from dotenv import load_dotenv
from supabase import create_client, Client
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# Load environment variables
load_dotenv()


# ============================================================
# CONFIGURATION — All settings in one place
# ============================================================
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")

DEEPSEEK_KEY_RING = [
    key.strip() for key in os.getenv("DEEPSEEK_API_KEYS", "").split(",") if key.strip()
]
if not DEEPSEEK_KEY_RING:
    single_key = os.getenv("DEEPSEEK_API_KEY", "")
    if single_key:
        DEEPSEEK_KEY_RING = [single_key]

DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_SCOUT_MODEL = os.getenv("DEEPSEEK_SCOUT_MODEL", "deepseek-chat")
DEEPSEEK_SCHOLAR_MODEL = os.getenv("DEEPSEEK_SCHOLAR_MODEL", "deepseek-reasoner")

COIN_COST_SCAN = 1
COIN_COST_DEEP = 2
COIN_COST_SMTP = 3
COIN_FREE_TRIAL = 50
CACHE_FRESHNESS_DAYS = 30
TASK_POLL_INTERVAL = 3
TASK_BATCH_SIZE = 10
PORT = int(os.getenv("PORT", 8000))
DEBUG = os.getenv("DEBUG", "false").lower() == "true"


# ============================================================
# SUPABASE CONNECTION
# ============================================================
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def get_supabase() -> Client:
    return supabase


# ============================================================
# DEEPSEEK AI MIDDLEWARE — Multi-key ring with 429 retry
# ============================================================
class CriticalError(Exception):
    pass

async def execute_llm_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not DEEPSEEK_KEY_RING:
        raise CriticalError("No DeepSeek API keys configured!")

    async with httpx.AsyncClient(timeout=60) as client:
        for key in DEEPSEEK_KEY_RING:
            try:
                response = await client.post(
                    DEEPSEEK_BASE_URL,
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json=payload,
                )
                if response.status_code == 429:
                    print(f"[DEEPSEEK] Key ...{key[-4:]} rate limited — trying next")
                    continue
                if response.status_code == 200:
                    return response.json()
                print(f"[DEEPSEEK] Key ...{key[-4:]} error {response.status_code}")
                continue
            except httpx.TimeoutException:
                print(f"[DEEPSEEK] Key ...{key[-4:]} timed out")
                continue
            except Exception as e:
                print(f"[DEEPSEEK] Key ...{key[-4:]} exception: {e}")
                continue

    raise CriticalError("ALL_KEYS_EXHAUSTED — All DeepSeek API keys are rate-limited or failing")


# ============================================================
# STEALTH FETCHER — Scrapling Fetcher (curl_cffi, NO browser)
# ============================================================
try:
    from scrapling import Fetcher as ScraplingFetcher
    _fetcher = ScraplingFetcher()
    USE_SCRAPLING = True
    print("[STEALTH] Scrapling Fetcher loaded — curl_cffi TLS impersonation active")
except ImportError:
    USE_SCRAPLING = False
    print("[STEALTH] Scrapling not available — falling back to httpx")

async def stealth_fetch(url: str, timeout: int = 30) -> Optional[Dict[str, Any]]:
    if USE_SCRAPLING:
        try:
            response = _fetcher.get(url, impersonate="chrome", timeout=timeout)
            if response and response.status == 200:
                return {"status": 200, "html": response.text, "url": str(response.url), "content": response.text}
            status = response.status if response else "No response"
            print(f"[STEALTH] HTTP {status} for {url}")
        except Exception as e:
            print(f"[STEALTH] Scrapling error for {url}: {e}")
    else:
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                response = await client.get(url, headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.5",
                })
                if response.status_code == 200:
                    return {"status": 200, "html": response.text, "url": str(response.url), "content": response.text}
        except Exception as e:
            print(f"[STEALTH] httpx error for {url}: {e}")
    return None


# ============================================================
# HASH DEDUP — SHA-256 + Global Cache Check
# ============================================================
def compute_hash(url: str) -> str:
    if not url or url == "ABSENT":
        url = "unknown"
    url = url.lower().strip().rstrip("/")
    return hashlib.sha256(url.encode("utf-8")).hexdigest()

async def check_duplicate(domain_hash: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
    try:
        db = get_supabase()
        result = db.table("global_intelligence_cache").select("*").eq("domain_hash", domain_hash).execute()
        if result.data and len(result.data) > 0:
            cached = result.data[0]
            last_verified = cached.get("last_verified_at")
            if last_verified:
                if isinstance(last_verified, str):
                    last_verified = datetime.fromisoformat(last_verified.replace("Z", "+00:00"))
                if datetime.now(last_verified.tzinfo) - last_verified < timedelta(days=CACHE_FRESHNESS_DAYS):
                    print(f"[DEDUP] Cache HIT for {domain_hash[:12]}... — 0 coins")
                    return True, cached
                else:
                    print(f"[DEDUP] Cache STALE for {domain_hash[:12]}... — re-scraping")
                    return False, None
            return True, cached
        return False, None
    except Exception as e:
        print(f"[DEDUP] Cache check error: {e}")
        return False, None

async def save_to_cache(lead: Dict[str, Any]) -> bool:
    try:
        db = get_supabase()
        cache_data = {
            "domain_hash": lead.get("domain_hash"),
            "company_name": lead.get("company_name", "ABSENT"),
            "website_url": lead.get("website_url", "ABSENT"),
            "dm_name": lead.get("dm_name", "ABSENT"),
            "dm_position": lead.get("dm_position", "ABSENT"),
            "verified_email": lead.get("verified_email", "ABSENT"),
            "is_catchall": lead.get("is_catchall", False),
            "linkedin": lead.get("linkedin", "ABSENT"),
            "instagram": lead.get("instagram", "ABSENT"),
            "phone": lead.get("phone", "ABSENT"),
        }
        db.table("global_intelligence_cache").upsert(cache_data, on_conflict="domain_hash").execute()
        print(f"[DEDUP] Saved {lead.get('company_name')} to global cache")
        return True
    except Exception as e:
        print(f"[DEDUP] Save error: {e}")
        return False


# ============================================================
# GATE 1: DNS Resolution Check (ALL tiers)
# ============================================================
async def check_dns(website_url: str) -> bool:
    try:
        if not website_url or website_url == "ABSENT":
            return False
        parsed = urlparse(website_url)
        domain = parsed.hostname or parsed.path.split("/")[0]
        if not domain:
            return False
        resolver = dns.resolver.Resolver()
        resolver.timeout = 5
        resolver.lifetime = 5
        answers = resolver.resolve(domain, "A")
        return len(answers) > 0
    except dns.resolver.NXDOMAIN:
        print(f"[GATE1-DNS] {website_url} — NXDOMAIN")
        return False
    except dns.resolver.NoAnswer:
        print(f"[GATE1-DNS] {website_url} — No A record")
        return False
    except dns.resolver.Timeout:
        print(f"[GATE1-DNS] {website_url} — Timeout")
        return False
    except Exception as e:
        print(f"[GATE1-DNS] {website_url} — Error: {e}")
        return False


# ============================================================
# GATE 2: Footprint Check (Starter+ tiers)
# ============================================================
def check_footprint(lead: Dict[str, Any]) -> bool:
    contact_found = False
    email = lead.get("verified_email", "ABSENT")
    if email != "ABSENT" and email and _looks_like_email(email):
        contact_found = True
    phone = lead.get("phone", "ABSENT")
    if phone != "ABSENT" and phone and _looks_like_phone(phone):
        contact_found = True
    linkedin = lead.get("linkedin", "ABSENT")
    if linkedin != "ABSENT" and linkedin and "linkedin" in linkedin.lower():
        contact_found = True
    instagram = lead.get("instagram", "ABSENT")
    if instagram != "ABSENT" and instagram and "instagram" in instagram.lower():
        contact_found = True
    dm_name = lead.get("dm_name", "ABSENT")
    if dm_name != "ABSENT" and dm_name and len(dm_name) > 2:
        contact_found = True
    if not contact_found:
        print(f"[GATE2-FOOTPRINT] {lead.get('company_name', 'Unknown')} — No contact method found")
    return contact_found

def _looks_like_email(text: str) -> bool:
    return bool(re.match(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$', text.strip()))

def _looks_like_phone(text: str) -> bool:
    return len(re.sub(r'[^0-9]', '', text)) >= 7


# ============================================================
# GATE 3: SMTP Handshake Check (Pro tier ONLY)
# ============================================================
async def check_smtp(email_address: str) -> Tuple[bool, bool]:
    if not email_address or email_address == "ABSENT":
        return False, False
    try:
        domain = email_address.split("@")[1]
        mx_records = dns.resolver.resolve(domain, "MX")
        if not mx_records:
            print(f"[GATE3-SMTP] No MX record for {domain}")
            return False, False
        mx_record = str(sorted(mx_records, key=lambda r: r.preference)[0].exchange).rstrip(".")
    except Exception as e:
        print(f"[GATE3-SMTP] MX lookup failed for {domain}: {e}")
        return False, False
    try:
        server = smtplib.SMTP(timeout=10)
        server.connect(mx_record, 25)
        server.ehlo("verify.baddecision.ai")
        server.mail("verify@baddecision.ai")
        code, message = server.rcpt(email_address)
        target_exists = code == 250
        random_user = "".join(random.choices(string.ascii_lowercase, k=15))
        fake_email = f"{random_user}@{domain}"
        code_fake, _ = server.rcpt(fake_email)
        is_catchall = code_fake == 250
        server.quit()
        if is_catchall:
            print(f"[GATE3-SMTP] {email_address} — CATCH-ALL DETECTED")
            return True, True
        if target_exists:
            print(f"[GATE3-SMTP] {email_address} — VERIFIED")
            return True, False
        print(f"[GATE3-SMTP] {email_address} — REJECTED")
        return False, False
    except Exception as e:
        print(f"[GATE3-SMTP] {email_address} — Error: {e}")
        return True, False


# ============================================================
# ENGINE 1: Ads Intent — Businesses running ads
# ============================================================
async def run_ads_intent(query: str, user_tier: str = "free") -> List[Dict[str, Any]]:
    leads = []
    search_prompt = f"""You are a business intelligence researcher. Find businesses currently running online advertisements related to: "{query}"
Search in Meta Ad Library, Google Ads Transparency Center, and TikTok Ads.
For each business provide: company_name, website_url, ad_platform.
Return a JSON array of objects. If you cannot find data, write "ABSENT". Find up to 25 businesses."""

    try:
        response = await execute_llm_payload({
            "model": DEEPSEEK_SCOUT_MODEL,
            "messages": [
                {"role": "system", "content": "You are a precise business data extractor. Always respond with valid JSON. Never use null — use 'ABSENT' for missing data."},
                {"role": "user", "content": search_prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        })
        content = response.get("choices", [{}])[0].get("message", {}).get("content", "{}")
        try:
            parsed = json.loads(content)
            businesses = parsed.get("businesses", parsed.get("results", []))
            if isinstance(parsed, list):
                businesses = parsed
        except json.JSONDecodeError:
            businesses = []
    except Exception as e:
        print(f"[ADS_INTENT] DeepSeek search error: {e}")
        businesses = []

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

        if website_url != "ABSENT":
            dns_ok = await check_dns(website_url)
            if not dns_ok:
                print(f"[ADS_INTENT] DNS failed for {website_url} — DROPPED")
                continue

        enrichment = await _enrich_lead(company_name, website_url, user_tier)
        lead = {
            "domain_hash": domain_hash, "company_name": company_name, "website_url": website_url,
            "dm_name": enrichment.get("dm_name", "ABSENT"), "dm_position": enrichment.get("dm_position", "ABSENT"),
            "verified_email": enrichment.get("verified_email", "ABSENT"), "is_catchall": False,
            "linkedin": enrichment.get("linkedin", "ABSENT"), "instagram": enrichment.get("instagram", "ABSENT"),
            "phone": enrichment.get("phone", "ABSENT"), "ad_platform": ad_platform,
        }

        if user_tier in ("starter", "growth", "pro"):
            footprint_ok = check_footprint(lead)
            if not footprint_ok:
                print(f"[ADS_INTENT] Footprint failed for {company_name} — DROPPED")
                continue

        if user_tier == "pro" and lead["verified_email"] != "ABSENT":
            smtp_ok, is_catchall = await check_smtp(lead["verified_email"])
            lead["is_catchall"] = is_catchall
            if not smtp_ok and not is_catchall:
                print(f"[ADS_INTENT] SMTP failed for {lead['verified_email']} — DROPPED")
                continue

        leads.append(lead)
    return leads


# ============================================================
# ENGINE 2: SMB Maps — Local brick & mortar businesses
# ============================================================
async def run_smb_maps(query: str, user_tier: str = "free") -> List[Dict[str, Any]]:
    leads = []
    search_prompt = f"""You are a local business researcher. Find small local businesses related to: "{query}"
Search Google Maps and local directories.
HARD RULES: Each business MUST have fewer than 50 employees, MUST have a physical address, NO chains.
For each provide: company_name, website_url, address, employee_count.
Return a JSON array. Find up to 25. Write "ABSENT" for missing data."""

    try:
        response = await execute_llm_payload({
            "model": DEEPSEEK_SCOUT_MODEL,
            "messages": [
                {"role": "system", "content": "You are a precise business data extractor. Always respond with valid JSON. Never use null — use 'ABSENT'."},
                {"role": "user", "content": search_prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        })
        content = response.get("choices", [{}])[0].get("message", {}).get("content", "{}")
        try:
            parsed = json.loads(content)
            businesses = parsed.get("businesses", parsed.get("results", []))
            if isinstance(parsed, list):
                businesses = parsed
        except json.JSONDecodeError:
            businesses = []
    except Exception as e:
        print(f"[SMB_MAPS] DeepSeek search error: {e}")
        businesses = []

    for biz in businesses[:25]:
        company_name = biz.get("company_name", "ABSENT")
        website_url = biz.get("website_url", "ABSENT")
        address = biz.get("address", "ABSENT")
        employee_count = biz.get("employee_count", 999)

        if company_name == "ABSENT" or not company_name:
            continue
        try:
            if int(employee_count) >= 50:
                print(f"[SMB_MAPS] {company_name} has {employee_count} employees — DROPPED")
                continue
        except (ValueError, TypeError):
            pass
        if address == "ABSENT" or not address:
            print(f"[SMB_MAPS] {company_name} no address — DROPPED")
            continue

        url_to_hash = website_url if website_url != "ABSENT" else company_name
        domain_hash = compute_hash(url_to_hash)
        is_dup, cached_data = await check_duplicate(domain_hash)
        if is_dup and cached_data:
            leads.append(cached_data)
            continue

        if website_url != "ABSENT":
            dns_ok = await check_dns(website_url)
            if not dns_ok:
                print(f"[SMB_MAPS] DNS failed for {website_url} — DROPPED")
                continue

        enrichment = await _enrich_local_lead(company_name, website_url, address, user_tier)
        lead = {
            "domain_hash": domain_hash, "company_name": company_name, "website_url": website_url,
            "dm_name": enrichment.get("dm_name", "ABSENT"), "dm_position": enrichment.get("dm_position", "ABSENT"),
            "verified_email": enrichment.get("verified_email", "ABSENT"), "is_catchall": False,
            "linkedin": enrichment.get("linkedin", "ABSENT"), "instagram": enrichment.get("instagram", "ABSENT"),
            "phone": enrichment.get("phone", "ABSENT"), "address": address,
        }

        if user_tier in ("starter", "growth", "pro"):
            footprint_ok = check_footprint(lead)
            if not footprint_ok:
                continue

        if user_tier == "pro" and lead["verified_email"] != "ABSENT":
            smtp_ok, is_catchall = await check_smtp(lead["verified_email"])
            lead["is_catchall"] = is_catchall
            if not smtp_ok and not is_catchall:
                continue

        leads.append(lead)
    return leads


# ============================================================
# ENGINE 3: Web-Absent — Businesses without websites
# ============================================================
async def run_web_absent(query: str, user_tier: str = "free") -> List[Dict[str, Any]]:
    leads = []
    search_prompt = f"""You are a business researcher. Find businesses on aggregator platforms related to: "{query}"
Search Yelp, Houzz, Zillow, Etsy, Amazon Storefronts.
HARD RULE: The business must NOT have its own standalone website. If it has an external website link, EXCLUDE it.
For each provide: company_name, aggregator_source, aggregator_url, has_external_website (must be false).
Return a JSON array. Find up to 25. Write "ABSENT" for missing data."""

    try:
        response = await execute_llm_payload({
            "model": DEEPSEEK_SCOUT_MODEL,
            "messages": [
                {"role": "system", "content": "You are a precise business data extractor. Always respond with valid JSON. Never use null — use 'ABSENT'."},
                {"role": "user", "content": search_prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        })
        content = response.get("choices", [{}])[0].get("message", {}).get("content", "{}")
        try:
            parsed = json.loads(content)
            businesses = parsed.get("businesses", parsed.get("results", []))
            if isinstance(parsed, list):
                businesses = parsed
        except json.JSONDecodeError:
            businesses = []
    except Exception as e:
        print(f"[WEB_ABSENT] DeepSeek search error: {e}")
        businesses = []

    for biz in businesses[:25]:
        company_name = biz.get("company_name", "ABSENT")
        aggregator_source = biz.get("aggregator_source", "ABSENT")
        aggregator_url = biz.get("aggregator_url", "ABSENT")
        has_external_website = biz.get("has_external_website", True)

        if company_name == "ABSENT" or not company_name:
            continue
        if has_external_website is True or has_external_website == "true":
            print(f"[WEB_ABSENT] {company_name} has external website — DROPPED")
            continue

        url_to_hash = aggregator_url if aggregator_url != "ABSENT" else company_name
        domain_hash = compute_hash(url_to_hash)
        is_dup, cached_data = await check_duplicate(domain_hash)
        if is_dup and cached_data:
            leads.append(cached_data)
            continue

        enrichment = await _enrich_aggregator_lead(company_name, aggregator_source, aggregator_url, user_tier)
        lead = {
            "domain_hash": domain_hash, "company_name": company_name, "website_url": aggregator_url,
            "dm_name": enrichment.get("dm_name", "ABSENT"), "dm_position": enrichment.get("dm_position", "ABSENT"),
            "verified_email": enrichment.get("verified_email", "ABSENT"), "is_catchall": False,
            "linkedin": "ABSENT", "instagram": "ABSENT", "phone": enrichment.get("phone", "ABSENT"),
            "aggregator_source": aggregator_source, "aggregator_url": aggregator_url,
        }

        if user_tier in ("starter", "growth", "pro"):
            if not check_footprint(lead):
                continue

        if user_tier == "pro" and lead["verified_email"] != "ABSENT":
            smtp_ok, is_catchall = await check_smtp(lead["verified_email"])
            lead["is_catchall"] = is_catchall
            if not smtp_ok and not is_catchall:
                continue

        leads.append(lead)
    return leads


# ============================================================
# ENGINE 4: Social Intent — Real-time demand radar
# ============================================================
async def run_social_intent(query: str, user_tier: str = "free") -> List[Dict[str, Any]]:
    leads = []
    now = datetime.utcnow()
    sixty_minutes_ago = now - timedelta(minutes=60)
    time_str = sixty_minutes_ago.strftime("%Y-%m-%d %H:%M UTC")

    search_prompt = f"""You are a social media intelligence researcher. Find people actively asking for help or expressing buying intent related to: "{query}"
Search LinkedIn, Skool, Circle, GitHub Discussions, Telegram groups.
HARD RULES: Posts must be from the last 60 minutes (after {time_str}). We want BUYERS not sellers.
For each provide: name, platform, profile_url, intent_text.
Return a JSON array. Find up to 25. Write "ABSENT" for missing data."""

    try:
        response = await execute_llm_payload({
            "model": DEEPSEEK_SCOUT_MODEL,
            "messages": [
                {"role": "system", "content": "You are a precise social media data extractor. Always respond with valid JSON. Never use null — use 'ABSENT'."},
                {"role": "user", "content": search_prompt},
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
        print(f"[SOCIAL_INTENT] DeepSeek search error: {e}")
        people = []

    for person in people[:25]:
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
            "domain_hash": domain_hash, "company_name": name, "website_url": profile_url,
            "dm_name": name, "dm_position": "ABSENT", "verified_email": "ABSENT", "is_catchall": False,
            "linkedin": profile_url if "linkedin" in profile_url.lower() else "ABSENT",
            "instagram": "ABSENT", "phone": "ABSENT", "platform": platform, "intent_text": intent_text,
        }
        leads.append(lead)
    return leads


# ============================================================
# ENRICHMENT HELPERS — DeepSeek AI extracts contact details
# ============================================================
async def _enrich_lead(company_name: str, website_url: str, user_tier: str) -> Dict[str, str]:
    prompt = f"""Find the key decision maker for: "{company_name}" ({website_url})
Look for: dm_name (CEO/founder/owner full name), dm_position (job title), verified_email (work email), linkedin (LinkedIn URL), instagram (company Instagram URL), phone (company phone).
If you cannot find something, write "ABSENT". Return a single JSON object."""
    try:
        response = await execute_llm_payload({
            "model": DEEPSEEK_SCOUT_MODEL,
            "messages": [
                {"role": "system", "content": "You are a precise data extractor. Always respond with valid JSON. Never use null — use 'ABSENT'."},
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        })
        content = response.get("choices", [{}])[0].get("message", {}).get("content", "{}")
        return json.loads(content)
    except Exception as e:
        print(f"[ENRICH] Error for {company_name}: {e}")
        return {"dm_name": "ABSENT", "dm_position": "ABSENT", "verified_email": "ABSENT", "linkedin": "ABSENT", "instagram": "ABSENT", "phone": "ABSENT"}

async def _enrich_local_lead(company_name: str, website_url: str, address: str, user_tier: str) -> Dict[str, str]:
    prompt = f"""Find the owner/decision maker for: "{company_name}" at "{address}" ({website_url})
Look for: dm_name, dm_position, verified_email, linkedin, instagram, phone.
Write "ABSENT" for missing data. Return a single JSON object."""
    try:
        response = await execute_llm_payload({
            "model": DEEPSEEK_SCOUT_MODEL,
            "messages": [
                {"role": "system", "content": "You are a precise data extractor. Always respond with valid JSON. Never use null — use 'ABSENT'."},
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        })
        content = response.get("choices", [{}])[0].get("message", {}).get("content", "{}")
        return json.loads(content)
    except Exception as e:
        print(f"[ENRICH] Error for {company_name}: {e}")
        return {"dm_name": "ABSENT", "dm_position": "ABSENT", "verified_email": "ABSENT", "linkedin": "ABSENT", "instagram": "ABSENT", "phone": "ABSENT"}

async def _enrich_aggregator_lead(company_name: str, aggregator_source: str, aggregator_url: str, user_tier: str) -> Dict[str, str]:
    prompt = f"""Find contact details for: "{company_name}" on {aggregator_source} at {aggregator_url}
This business does NOT have its own website. Look for: dm_name, dm_position, verified_email, phone.
Write "ABSENT" for missing data. Return a single JSON object."""
    try:
        response = await execute_llm_payload({
            "model": DEEPSEEK_SCOUT_MODEL,
            "messages": [
                {"role": "system", "content": "You are a precise data extractor. Always respond with valid JSON. Never use null — use 'ABSENT'."},
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        })
        content = response.get("choices", [{}])[0].get("message", {}).get("content", "{}")
        return json.loads(content)
    except Exception as e:
        print(f"[ENRICH] Error for {company_name}: {e}")
        return {"dm_name": "ABSENT", "dm_position": "ABSENT", "verified_email": "ABSENT", "phone": "ABSENT"}


# ============================================================
# ENGINE MAP — task_type to engine function
# ============================================================
ENGINE_MAP = {
    "ads_intent": run_ads_intent,
    "smb_maps": run_smb_maps,
    "web_absent": run_web_absent,
    "social_intent": run_social_intent,
}


# ============================================================
# BACKGROUND TASK WORKER — Polls for pending tasks
# ============================================================
async def run_task_worker():
    print("=" * 60)
    print("  BAD DECISION AI — Task Worker Started")
    print(f"  Checking for new tasks every {TASK_POLL_INTERVAL} seconds")
    print("=" * 60)

    while True:
        try:
            tasks = await _fetch_pending_tasks()
            if tasks:
                print(f"[WORKER] Found {len(tasks)} pending task(s)")
                for task in tasks:
                    await _process_task(task)
            await asyncio.sleep(TASK_POLL_INTERVAL)
        except Exception as e:
            print(f"[WORKER] Error in main loop: {e}")
            await asyncio.sleep(TASK_POLL_INTERVAL)

async def _fetch_pending_tasks():
    try:
        db = get_supabase()
        result = db.table("tasks").select("*, profiles(tier)").eq("status", "pending").order("created_at", desc=False).limit(TASK_BATCH_SIZE).execute()
        return result.data or []
    except Exception as e:
        print(f"[WORKER] Error fetching tasks: {e}")
        return []

async def _process_task(task: Dict[str, Any]):
    task_id = task.get("id")
    user_id = task.get("user_id")
    task_type = task.get("task_type")
    query = task.get("query")
    coins_reserved = task.get("coins_reserved", 0)
    user_tier = "free"
    profile = task.get("profiles")
    if profile:
        user_tier = profile.get("tier", "free")

    print(f"[WORKER] Processing task {task_id}: {task_type} — '{query}' (tier: {user_tier})")
    await _update_task_status(task_id, "processing")

    try:
        engine_func = ENGINE_MAP.get(task_type)
        if not engine_func:
            print(f"[WORKER] Unknown task_type: {task_type}")
            await _update_task_status(task_id, "failed")
            return

        leads = await engine_func(query=query, user_tier=user_tier)

        if not leads:
            print(f"[WORKER] No leads found for task {task_id} — exhausted")
            await _update_task_status(task_id, "exhausted")
            return

        collection = await _create_smart_collection(user_id=user_id, name=query, task_type=task_type)
        for lead in leads:
            await save_to_cache(lead)
            if collection:
                await _link_lead_to_collection(collection_id=collection, lead_hash=lead.get("domain_hash"))

        if coins_reserved > 0:
            await _deduct_coins(user_id, coins_reserved)
            print(f"[WORKER] Deducted {coins_reserved} coins from user {user_id}")

        await _update_task_status(task_id, "completed")
        print(f"[WORKER] Task {task_id} completed — {len(leads)} leads found")

    except Exception as e:
        print(f"[WORKER] Task {task_id} FAILED: {e}")
        await _update_task_status(task_id, "failed")

async def _update_task_status(task_id: str, status: str):
    try:
        db = get_supabase()
        db.table("tasks").update({"status": status}).eq("id", task_id).execute()
    except Exception as e:
        print(f"[WORKER] Error updating task {task_id}: {e}")

async def _create_smart_collection(user_id: str, name: str, task_type: str) -> str:
    try:
        db = get_supabase()
        result = db.table("smart_collections").insert({"user_id": user_id, "name": name, "task_type": task_type}).execute()
        if result.data:
            collection_id = result.data[0].get("id")
            print(f"[WORKER] Created collection: {name} ({collection_id})")
            return collection_id
    except Exception as e:
        print(f"[WORKER] Error creating collection: {e}")
    return None

async def _link_lead_to_collection(collection_id: str, lead_hash: str):
    try:
        db = get_supabase()
        db.table("workspace_leads").insert({"collection_id": collection_id, "lead_hash": lead_hash}).execute()
    except Exception as e:
        print(f"[WORKER] Error linking lead: {e}")

async def _deduct_coins(user_id: str, amount: int):
    try:
        db = get_supabase()
        db.rpc("deduct_coins", {"p_user_id": user_id, "p_amount": amount}).execute()
    except Exception as e:
        print(f"[WORKER] Error deducting coins: {e}")


# ============================================================
# FASTAPI WEB SERVER
# ============================================================
app = FastAPI(
    title="Bad Decision AI — Backend Engine",
    description="The scraping and validation engine that powers Bad Decision AI",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return {"status": "alive", "service": "Bad Decision AI Backend", "version": "1.0.0"}

@app.get("/health")
def health_check():
    try:
        db = get_supabase()
        db.table("profiles").select("id").limit(1).execute()
        db_status = "connected"
    except Exception as e:
        db_status = f"error: {str(e)}"
    return {"status": "healthy", "database": db_status}

@app.post("/api/tasks/create")
async def create_task(user_id: str, task_type: str, query: str, coins_reserved: int = 0):
    db = get_supabase()
    result = db.table("tasks").insert({
        "user_id": user_id, "task_type": task_type, "query": query,
        "status": "pending", "coins_reserved": coins_reserved,
    }).execute()
    return {"success": True, "task": result.data}

@app.get("/api/tasks/{user_id}")
async def get_user_tasks(user_id: str):
    db = get_supabase()
    result = db.table("tasks").select("*").eq("user_id", user_id).order("created_at", desc=True).execute()
    return {"tasks": result.data}

@app.get("/api/leads/{collection_id}")
async def get_collection_leads(collection_id: str):
    db = get_supabase()
    result = db.table("workspace_leads").select("*, global_intelligence_cache(*)").eq("collection_id", collection_id).execute()
    return {"leads": result.data}

@app.get("/api/cache/check")
async def check_cache(company_name: str = "", website_url: str = ""):
    db = get_supabase()
    result = db.rpc("check_global_cache", {"p_company_name": company_name, "p_website_url": website_url}).execute()
    return {"cache_hits": result.data}

@app.post("/api/coins/deduct")
async def deduct_coins(user_id: str, amount: int):
    db = get_supabase()
    db.rpc("deduct_coins", {"p_user_id": user_id, "p_amount": amount}).execute()
    return {"success": True}

@app.post("/api/coins/add")
async def add_coins(user_id: str, amount: int):
    db = get_supabase()
    db.rpc("add_coins", {"p_user_id": user_id, "p_amount": amount}).execute()
    return {"success": True}


# ============================================================
# STARTUP — Launch the background worker
# ============================================================
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(run_task_worker())


# ============================================================
# RUN THE SERVER
# ============================================================
if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=DEBUG)
