"""
BAD DECISION — Backend Configuration
=====================================
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# SUPABASE
# ============================================================
def _clean_env(val: str) -> str:
    """Aggressively clean env var values — remove ALL hidden characters."""
    if not val:
        return ""
    # Remove ALL control characters, null bytes, carriage returns, newlines, tabs
    # that might be embedded anywhere in the string
    import re
    val = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', val)
    # Strip whitespace from both ends
    val = val.strip()
    # Remove any remaining non-printable characters
    val = ''.join(c for c in val if c.isprintable())
    return val

SUPABASE_URL = _clean_env(os.getenv("SUPABASE_URL", ""))
SUPABASE_KEY = _clean_env(os.getenv("SUPABASE_KEY", ""))
SUPABASE_ANON_KEY = _clean_env(os.getenv("SUPABASE_ANON_KEY", ""))

# ============================================================
# BACKEND API SECRET + CORS
# ============================================================
BACKEND_API_SECRET = os.getenv("BACKEND_API_SECRET", "").strip()
ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "https://bad-decision-front-end.vercel.app").strip()

# ============================================================
# DEEPSEEK
# ============================================================
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
DEEPSEEK_SCOUT_MODEL = os.getenv("DEEPSEEK_SCOUT_MODEL", "deepseek-chat")
DEEPSEEK_SCHOLAR_MODEL = os.getenv("DEEPSEEK_SCHOLAR_MODEL", "deepseek-reasoner")

# ============================================================
# SERPER.DEV
# ============================================================
SERPER_API_KEY = os.getenv("SERPER_API_KEY", "").strip()
SERPER_BASE_URL = "https://google.serper.dev"

# ============================================================
# SCRAPINGANT — Cloud scraping API for JS-rendered sites
# ============================================================
SCRAPINGANT_API_KEY = os.getenv("SCRAPINGANT_API_KEY", "").strip()
SCRAPINGANT_BASE_URL = "https://api.scrapingant.com/v2"

# ============================================================
# SEC EDGAR — US public company officers (free, no key)
# ============================================================
SEC_EDGAR_BASE_URL = "https://data.sec.gov"
SEC_EDGAR_USER_AGENT = os.getenv("SEC_EDGAR_USER_AGENT", "bad-decision/1.0 (contact@baddecision.app)").strip()

# ============================================================
# COMPANIES HOUSE — UK company officers (free, requires API key)
# ============================================================
COMPANIES_HOUSE_API_KEY = os.getenv("COMPANIES_HOUSE_API_KEY", "").strip()
COMPANIES_HOUSE_BASE_URL = "https://api.company-information.service.gov.uk"

# ============================================================
# OPENSTREETMAP
# ============================================================
OSM_NOMINATIM_USER_AGENT = os.getenv("OSM_NOMINATIM_USER_AGENT", "bad-decision/1.0 (contact@baddecision.app)").strip()
OSM_OVERPASS_ENDPOINT = os.getenv("OSM_OVERPASS_ENDPOINT", "https://overpass-api.de/api/interpreter").strip()

# ============================================================
# CREDIT ECONOMY
# ============================================================
# Per-action credit costs (Phase B pricing overhaul)
CREDIT_COST_SCAN = 1       # Free tier: 1 credit per lead (smb_maps only)
CREDIT_COST_DEEP = 2       # Starter/Growth: 2 credits per lead
CREDIT_COST_SMTP = 3       # Pro: 3 credits per lead (includes DeepSeek Gate 3)
CREDIT_COST_MSG_GEN_SINGLE = 3   # Single-lead outreach message generation (4 messages)
CREDIT_COST_MSG_GEN_BATCH = 2    # Batch mode per-lead (skip_regeneration=True)
CREDIT_COST_REGENERATE = 2       # Regenerate a single message
CREDIT_COST_EMAIL_SEND = 1       # Per 5 emails sent (0.2 credits per email)
CREDIT_COST_AI_TURN = 2          # AI Agent conversation turn (Phase F)

# Free tier
CREDIT_FREE_TRIAL = 50            # Was 100 — reduced to drive paid conversion
CREDIT_FREE_RENEWAL_DAYS = 30     # Free credits expire/renew every 30 days
CREDIT_PAID_EXPIRY_DAYS = 60      # Paid credits expire 60 days after purchase

# Lead targets — credit-aware (engine will check user balance)
LEAD_TARGET_FREE = 25       # Free tier cap (was 50 — reduced for free tier)
LEAD_TARGET_STARTER = 50    # Starter tier cap
LEAD_TARGET_GROWTH = 75     # Growth tier cap
LEAD_TARGET_PAID = 100      # Pro tier cap
MIN_LEADS_WARNING = 10      # Show warning if user can't afford this many

# ============================================================
# CACHE FRESHNESS
# ============================================================
CACHE_FRESHNESS_DAYS = 30

# ============================================================
# TASK POLLING + CONCURRENCY
# ============================================================
TASK_POLL_INTERVAL = 3
TASK_BATCH_SIZE = 5
MAX_CONCURRENT_TASKS = 2
MAX_CONCURRENT_SOURCES = 5    # Increased — we now have more sources
MAX_CONCURRENT_LEADS = 10     # Increased — email scraper is lightweight

# ============================================================
# TIMEOUTS
# ============================================================
SOURCE_TIMEOUT = 15
SCRAPINGANT_TIMEOUT = 30      # ScrapingAnt needs more time (JS rendering)
TASK_TIMEOUT = 180            # 3 minutes for larger searches
SMTP_TIMEOUT = 10
DEEPSEEK_TIMEOUT = 60

# ============================================================
# RATE LIMITING
# ============================================================
RATE_LIMIT_SEARCHES_PER_MINUTE = 5
RATE_LIMIT_API_PER_MINUTE = 60

# ============================================================
# RESEND — Transactional Email
# ============================================================
# Only sends if RESEND_API_KEY is set. Silent no-op otherwise.
# Get a key from https://resend.com → API Keys.
# From-email must be on a domain verified in Resend dashboard.
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
RESEND_FROM_EMAIL = os.getenv("RESEND_FROM_EMAIL", "Bad Decision <noreply@baddecision.app>").strip()
RESEND_API_BASE = "https://api.resend.com/emails"

# ============================================================
# SENTRY — Error Tracking
# ============================================================
# Optional. If SENTRY_DSN is not set, Sentry is silently skipped.
# Get a DSN from https://sentry.io → Settings → Projects → Bad Decision Backend → Client Keys (DSN)
SENTRY_DSN = os.getenv("SENTRY_DSN", "").strip()
SENTRY_ENVIRONMENT = os.getenv("SENTRY_ENVIRONMENT", "production").strip()  # production | staging | development
SENTRY_TRACES_SAMPLE_RATE = float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1"))  # 10% of transactions traced

# ============================================================
# SERVER
# ============================================================
PORT = int(os.getenv("PORT", 8000))
DEBUG = os.getenv("DEBUG", "false").lower() == "true"
