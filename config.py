"""
BAD DECISION AI — Backend Configuration
========================================
This file holds all the settings the backend needs.
It reads secret keys from "environment variables" (like a locked safe)
so we never hardcode passwords in the code.
"""

import os
from dotenv import load_dotenv

# Load the .env file (this holds our secret keys locally)
load_dotenv()


# ============================================================
# SUPABASE — The Database
# ============================================================
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")  # This is the SERVICE ROLE key (full access)
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")  # Public key (limited access)


# ============================================================
# DEEPSEEK — The AI Brain
# ============================================================
# We support MULTIPLE API keys so when one gets rate-limited (too many requests),
# we automatically switch to the next one.
# Put your keys separated by commas in the .env file
DEEPSEEK_KEY_RING = [
    key.strip()
    for key in os.getenv("DEEPSEEK_API_KEYS", "").split(",")
    if key.strip()
]

# If someone only has one key, that's fine too
if not DEEPSEEK_KEY_RING:
    single_key = os.getenv("DEEPSEEK_API_KEY", "")
    if single_key:
        DEEPSEEK_KEY_RING = [single_key]

DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1/chat/completions"

# Which DeepSeek model to use for each job
DEEPSEEK_SCOUT_MODEL = os.getenv("DEEPSEEK_SCOUT_MODEL", "deepseek-chat")      # For Gate 1 & 2
DEEPSEEK_SCHOLAR_MODEL = os.getenv("DEEPSEEK_SCHOLAR_MODEL", "deepseek-reasoner")  # For Gate 3


# ============================================================
# COIN ECONOMY — How much each operation costs
# ============================================================
COIN_COST_SCAN = 1       # Live Internet Scan (Gate 1 only)
COIN_COST_DEEP = 2       # AI Deep Finder (Gate 1 & 2)
COIN_COST_SMTP = 3       # Guaranteed Human Target (Gate 1, 2 & 3)
COIN_FREE_TRIAL = 250      # Coins given to new free users


# ============================================================
# PRICING TIERS (Nigerian Naira)
# ============================================================
PRICING_TIERS = {
    "free": {
        "name": "Scout",
        "price_monthly": 0,
        "currency": "NGN",
        "coins_per_month": 1000,
        "engines": ["ads_intent", "smb_maps", "web_absent", "social_intent"],
        "max_leads_per_search": 10,
        "email_verification": "basic",  # regex + website scrape only
        "export_formats": ["csv"],
        "parallel_searches": 1,
        "support": "community",
    },
    "starter": {
        "name": "Hunter",
        "price_monthly": 10000,
        "currency": "NGN",
        "coins_per_month": 5000,
        "engines": ["ads_intent", "smb_maps", "web_absent", "social_intent"],
        "max_leads_per_search": 25,
        "email_verification": "verified",  # + pattern prediction + SMTP
        "export_formats": ["csv", "json"],
        "parallel_searches": 2,
        "support": "email",
    },
    "growth": {
        "name": "Commander",
        "price_monthly": 25000,
        "currency": "NGN",
        "coins_per_month": 15000,
        "engines": ["ads_intent", "smb_maps", "web_absent", "social_intent"],
        "max_leads_per_search": 50,
        "email_verification": "deep",  # + Hunter.io + Firecrawl
        "export_formats": ["csv", "json", "excel"],
        "parallel_searches": 5,
        "support": "priority_email",
    },
    "pro": {
        "name": "Overlord",
        "price_monthly": 50000,
        "currency": "NGN",
        "coins_per_month": 50000,
        "engines": ["ads_intent", "smb_maps", "web_absent", "social_intent"],
        "max_leads_per_search": 100,
        "email_verification": "guaranteed",  # All strategies + SMTP verification
        "export_formats": ["csv", "json", "excel", "api"],
        "parallel_searches": 10,
        "support": "dedicated",
    },
}

COIN_TOPUP_PACKS = {
    "micro": {"coins": 500, "price": 2000},
    "small": {"coins": 1500, "price": 5000},
    "medium": {"coins": 5000, "price": 15000},
    "large": {"coins": 15000, "price": 40000},
}


# ============================================================
# CACHE FRESHNESS — How long before data is "stale"
# ============================================================
CACHE_FRESHNESS_DAYS = 30  # If data was verified within 30 days, it's still fresh


# ============================================================
# TASK POLLING — How often the worker checks for new tasks
# ============================================================
TASK_POLL_INTERVAL = 3    # Check every 3 seconds
TASK_BATCH_SIZE = 10      # Pick up this many tasks at once


# ============================================================
# NEW API KEYS — Free tier services
# ============================================================
SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")
FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY", "")
HUNTER_API_KEY = os.getenv("HUNTER_API_KEY", "")
OPENCORPORATES_API_TOKEN = os.getenv("OPENCORPORATES_API_TOKEN", "")

# ============================================================
# PAYSTACK — Payment Processing
# ============================================================
PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY", "")
PAYSTACK_BASE_URL = "https://api.paystack.co"


# ============================================================
# SERVER SETTINGS
# ============================================================
PORT = int(os.getenv("PORT", 8000))
DEBUG = os.getenv("DEBUG", "false").lower() == "true"
