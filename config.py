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
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "").strip()

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
CREDIT_COST_SCAN = 1       # Free tier: 1 credit per lead
CREDIT_COST_DEEP = 2       # Starter/Growth: 2 credits per lead
CREDIT_COST_SMTP = 3       # Pro: 3 credits per lead
CREDIT_FREE_TRIAL = 50

# Lead targets — credit-aware (engine will check user balance)
LEAD_TARGET_FREE = 50      # Free tier cap
LEAD_TARGET_PAID = 150     # Paid tier cap (target 100+ after filtering)
MIN_LEADS_WARNING = 10     # Show warning if user can't afford this many

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
# SERVER
# ============================================================
PORT = int(os.getenv("PORT", 8000))
DEBUG = os.getenv("DEBUG", "false").lower() == "true"
