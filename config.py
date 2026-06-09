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
COIN_FREE_TRIAL = 1000   # Coins given to new free users (raised from 50)


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
# SERVER SETTINGS
# ============================================================
PORT = int(os.getenv("PORT", 8000))
DEBUG = os.getenv("DEBUG", "false").lower() == "true"
