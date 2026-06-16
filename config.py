"""
BAD DECISION AI — Backend Configuration
========================================
Holds all the settings the backend needs.
Reads secret keys from environment variables.
"""

import os
from dotenv import load_dotenv

# Load the .env file (holds our secret keys locally)
load_dotenv()


# ============================================================
# SUPABASE — The Database
# ============================================================
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()  # SERVICE ROLE key (full access)
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "").strip()  # Public key (limited)


# ============================================================
# BACKEND API SECRET — shared with the Next.js frontend
# ============================================================
BACKEND_API_SECRET = os.getenv("BACKEND_API_SECRET", "").strip()


# ============================================================
# DEEPSEEK — The AI Brain
# ============================================================
# Multiple keys supported — separate with commas
DEEPSEEK_KEY_RING = [
    key.strip()
    for key in os.getenv("DEEPSEEK_API_KEYS", "").split(",")
    if key.strip()
]

if not DEEPSEEK_KEY_RING:
    single_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if single_key:
        DEEPSEEK_KEY_RING = [single_key]

DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1/chat/completions"

# Which DeepSeek model to use for each job
DEEPSEEK_SCOUT_MODEL = os.getenv("DEEPSEEK_SCOUT_MODEL", "deepseek-chat")
DEEPSEEK_SCHOLAR_MODEL = os.getenv("DEEPSEEK_SCHOLAR_MODEL", "deepseek-reasoner")


# ============================================================
# COIN ECONOMY
# ============================================================
COIN_COST_SCAN = 1       # Live Internet Scan (Gate 1 only)
COIN_COST_DEEP = 2       # AI Deep Finder (Gate 1 & 2)
COIN_COST_SMTP = 3       # Guaranteed Human Target (Gate 1, 2 & 3)
COIN_FREE_TRIAL = 50     # Coins given to new free users


# ============================================================
# CACHE FRESHNESS
# ============================================================
CACHE_FRESHNESS_DAYS = 30


# ============================================================
# TASK POLLING
# ============================================================
TASK_POLL_INTERVAL = 3
TASK_BATCH_SIZE = 10


# ============================================================
# SERVER SETTINGS
# ============================================================
PORT = int(os.getenv("PORT", 8000))
DEBUG = os.getenv("DEBUG", "false").lower() == "true"
