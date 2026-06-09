"""
BAD DECISION AI — Backend Configuration
========================================
All settings for the backend. Reads secrets from environment variables.
"""

import os
from dotenv import load_dotenv

load_dotenv()


# ============================================================
# SUPABASE — The Database
# ============================================================
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")  # SERVICE ROLE key (full access)
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")  # Public key (limited)


# ============================================================
# DEEPSEEK — The AI Brain
# ============================================================
DEEPSEEK_KEY_RING = [
    key.strip()
    for key in os.getenv("DEEPSEEK_API_KEYS", "").split(",")
    if key.strip()
]

if not DEEPSEEK_KEY_RING:
    single_key = os.getenv("DEEPSEEK_API_KEY", "")
    if single_key:
        DEEPSEEK_KEY_RING = [single_key]

DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_SCOUT_MODEL = os.getenv("DEEPSEEK_SCOUT_MODEL", "deepseek-chat")
DEEPSEEK_SCHOLAR_MODEL = os.getenv("DEEPSEEK_SCHOLAR_MODEL", "deepseek-reasoner")


# ============================================================
# SERPER.DEV — Google Search API
# ============================================================
SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")


# ============================================================
# FIRECRAWL — Web Scraping API
# ============================================================
FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY", "")


# ============================================================
# HUNTER.IO — Email Finder API
# ============================================================
HUNTER_API_KEY = os.getenv("HUNTER_API_KEY", "")


# ============================================================
# PAYSTACK — Payment Gateway (Nigeria)
# ============================================================
PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY", "")
PAYSTACK_PUBLIC_KEY = os.getenv("PAYSTACK_PUBLIC_KEY", "")
PAYSTACK_WEBHOOK_SECRET = os.getenv("PAYSTACK_WEBHOOK_SECRET", "")


# ============================================================
# COIN ECONOMY — Pricing Tiers
# ============================================================
COIN_COST_SCAN = 1       # Per lead found
COIN_COST_DEEP = 2       # Per lead with enrichment
COIN_COST_SMTP = 3       # Per lead with SMTP verification

COIN_FREE_TRIAL = 50     # Coins for free tier

# Pricing tiers (Nigerian Naira)
PRICING_TIERS = {
    "free": {
        "name": "Explorer",
        "price": 0,
        "coins_per_month": 50,
        "engines": ["ads_intent", "smb_maps"],
        "max_searches_per_day": 5,
        "csv_export": False,
        "email_verification": False,
        "smtp_verification": False,
        "ai_enrichment": False,
        "priority_processing": False,
    },
    "starter": {
        "name": "Starter",
        "price": 10000,
        "coins_per_month": 200,
        "engines": ["ads_intent", "smb_maps", "web_absent"],
        "max_searches_per_day": 25,
        "csv_export": True,
        "email_verification": True,
        "smtp_verification": False,
        "ai_enrichment": False,
        "priority_processing": False,
    },
    "growth": {
        "name": "Growth",
        "price": 25000,
        "coins_per_month": 600,
        "engines": ["ads_intent", "smb_maps", "web_absent", "social_intent"],
        "max_searches_per_day": 100,
        "csv_export": True,
        "email_verification": True,
        "smtp_verification": False,
        "ai_enrichment": True,
        "priority_processing": True,
    },
    "pro": {
        "name": "Pro",
        "price": 50000,
        "coins_per_month": 1500,
        "engines": ["ads_intent", "smb_maps", "web_absent", "social_intent"],
        "max_searches_per_day": -1,  # unlimited
        "csv_export": True,
        "email_verification": True,
        "smtp_verification": True,
        "ai_enrichment": True,
        "priority_processing": True,
    },
}

# Coin top-up packages (Naira)
COIN_PACKAGES = {
    "micro": {"coins": 100, "price": 5000},
    "standard": {"coins": 300, "price": 12000},
    "bulk": {"coins": 750, "price": 25000},
    "enterprise": {"coins": 2000, "price": 60000},
}


# ============================================================
# CACHE FRESHNESS
# ============================================================
CACHE_FRESHNESS_DAYS = 30


# ============================================================
# TASK WORKER SETTINGS
# ============================================================
TASK_POLL_INTERVAL = 3
TASK_BATCH_SIZE = 10


# ============================================================
# SERVER SETTINGS
# ============================================================
PORT = int(os.getenv("PORT", 8000))
DEBUG = os.getenv("DEBUG", "false").lower() == "true"
BACKEND_API_SECRET = os.getenv("BACKEND_API_SECRET", "")
