"""
BAD DECISION — Backend Configuration
=====================================
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
# ENFORCED: if this is empty, the backend refuses to start.
# The frontend MUST send this in the X-API-Secret header.
BACKEND_API_SECRET = os.getenv("BACKEND_API_SECRET", "").strip()

# Allowed CORS origin (the Vercel frontend URL).
# Set ALLOWED_ORIGIN env var to your production Vercel URL.
ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "https://bad-decision-front-end.vercel.app").strip()


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
# SERPER.DEV — Google Search API (primary search backend)
# ============================================================
# Returns Google search results as clean JSON. No JS rendering needed.
# Used for ALL Google queries instead of scraping Google directly.
SERPER_API_KEY = os.getenv("SERPER_API_KEY", "").strip()
SERPER_BASE_URL = "https://google.serper.dev"


# ============================================================
# OPENSTREETMAP — Free map data (Nominatim + Overpass)
# ============================================================
# Used for the smb_maps engine instead of Google Maps.
# Nominatim: geocoding (convert "Lagos, Nigeria" to coordinates).
# Overpass: query OSM for businesses by tag (amenity=cafe, shop=bakery, etc.).
OSM_NOMINATIM_USER_AGENT = os.getenv("OSM_NOMINATIM_USER_AGENT", "bad-decision/1.0 (contact@baddecision.app)").strip()
OSM_OVERPASS_ENDPOINT = os.getenv("OSM_OVERPASS_ENDPOINT", "https://overpass-api.de/api/interpreter").strip()


# ============================================================
# CREDIT ECONOMY (renamed from "coins" to "credits")
# ============================================================
CREDIT_COST_SCAN = 1       # Free tier: Gate 1 only (DNS) — 1 credit per lead
CREDIT_COST_DEEP = 2       # Starter/Growth: Gate 1 + 2 (DNS + SMTP) — 2 credits per lead
CREDIT_COST_SMTP = 3       # Pro: Gate 1 + 2 + 3 (DNS + SMTP + DeepSeek) — 3 credits per lead
CREDIT_FREE_TRIAL = 50     # Credits given to new free users

# Lead targets (tuned for Render FREE tier — 512MB RAM)
# Increased to return more leads per search.
LEAD_TARGET_FREE = 50      # Free tier: up to 50 leads per search
LEAD_TARGET_PAID = 100     # Paid tiers: up to 100 leads per search


# ============================================================
# CACHE FRESHNESS
# ============================================================
CACHE_FRESHNESS_DAYS = 30


# ============================================================
# TASK POLLING + CONCURRENCY (tuned for Render FREE tier)
# ============================================================
TASK_POLL_INTERVAL = 3     # Check for new tasks every 3 seconds
TASK_BATCH_SIZE = 5        # Fetch up to 5 pending tasks per poll
MAX_CONCURRENT_TASKS = 2   # Process at most 2 tasks at once (512MB RAM limit)
MAX_CONCURRENT_SOURCES = 3 # Fetch at most 3 sources in parallel per task
MAX_CONCURRENT_LEADS = 5   # Enrich at most 5 leads in parallel per task


# ============================================================
# TIMEOUTS (per free-tier constraints)
# ============================================================
SOURCE_TIMEOUT = 15        # Max seconds per source fetch
TASK_TIMEOUT = 120         # Max seconds per task (2 minutes)
SMTP_TIMEOUT = 10          # Max seconds per SMTP probe
DEEPSEEK_TIMEOUT = 60      # Max seconds per DeepSeek call


# ============================================================
# RATE LIMITING
# ============================================================
RATE_LIMIT_SEARCHES_PER_MINUTE = 5   # Max 5 searches per user per minute
RATE_LIMIT_API_PER_MINUTE = 60       # Max 60 API calls per IP per minute


# ============================================================
# SERVER SETTINGS
# ============================================================
PORT = int(os.getenv("PORT", 8000))
DEBUG = os.getenv("DEBUG", "false").lower() == "true"
