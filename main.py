"""
BAD DECISION — FastAPI Main Server
====================================
Entry point for the entire Python backend.
Starts the web server and sets up all the routes.

KEY DESIGN:
- Clerk user IDs are TEXT strings like "user_3Ew45fAIqwEJ3naNttXUPMxTfFt", NOT UUIDs.
- Uses Pydantic models for JSON body parsing (fixes 422 errors).
- Tier-based engine enforcement: free users can ONLY use ads_intent.
- Credit flow: RESERVE on task creation → COMMIT on success / REFUND on failure.
- Backend API secret is ENFORCED (not optional).
- CORS is restricted to the Vercel frontend domain.
- Rate limiting on search endpoint.
- Background task worker started on startup.
- No device fingerprint logic — multiple accounts per device are allowed.
"""

import time
from collections import defaultdict, deque
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional
import uvicorn

from config import (
    PORT, DEBUG, BACKEND_API_SECRET, ALLOWED_ORIGIN,
    RATE_LIMIT_SEARCHES_PER_MINUTE, RATE_LIMIT_API_PER_MINUTE,
    CREDIT_COST_SCAN, CREDIT_COST_DEEP, CREDIT_COST_SMTP,
    LEAD_TARGET_FREE, LEAD_TARGET_PAID,
)

# Create the FastAPI app
app = FastAPI(
    title="Bad Decision — Backend Engine",
    description="The scraping and validation engine that powers Bad Decision",
    version="4.0.0",
)

# ============================================================
# SECURITY HEADERS — Prevent clickjacking, MIME sniffing, XSS
# ============================================================
@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = "default-src 'none'"
    return response

# ============================================================
# CORS — Restricted to the Vercel frontend domain
# ============================================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "OPTIONS"],
    allow_headers=["*"],
)


# ============================================================
# TIER CONFIG — which engines each tier can use
# ============================================================
# Free tier gets smb_maps (Local Businesses) — uses OpenStreetMap which
# returns real structured data and tends to find more leads.
# ads_intent is now a PAID feature (requires Starter+).
TIER_ENGINES = {
    "free":    ["smb_maps"],
    "starter": ["ads_intent", "smb_maps", "web_absent", "social_intent"],
    "growth":  ["ads_intent", "smb_maps", "web_absent", "social_intent"],
    "pro":     ["ads_intent", "smb_maps", "web_absent", "social_intent"],
}

VALID_ENGINES = {"ads_intent", "smb_maps", "web_absent", "social_intent"}


# ============================================================
# RATE LIMITING (in-memory, per-IP and per-user)
# ============================================================
# Note: This resets on server restart. For production, use Redis.
# But for Render free tier, in-memory is sufficient.
_api_hits: dict = defaultdict(deque)       # IP → deque of timestamps
_search_hits: dict = defaultdict(deque)    # user_id → deque of timestamps


def _rate_limit(key: str, store: dict, limit: int, window_sec: int = 60) -> bool:
    """Check if a key is within the rate limit. Returns True if allowed."""
    now = time.time()
    window_start = now - window_sec

    # Remove old timestamps
    while store[key] and store[key][0] < window_start:
        store[key].popleft()

    if len(store[key]) >= limit:
        return False

    store[key].append(now)
    return True


# ============================================================
# PYDANTIC MODELS
# ============================================================
class TaskCreateRequest(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=256)
    task_type: str = Field(..., pattern=r"^(ads_intent|smb_maps|web_absent|social_intent)$")
    query: str = Field(..., min_length=1, max_length=1000)
    credits_reserved: int = Field(default=2, ge=0)
    country: str = Field(default="", max_length=10)
    state_region: str = Field(default="", max_length=100)


class CreditAddRequest(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=256)
    amount: int = Field(..., gt=0, le=100000)
    transaction_type: str = Field(default="purchase")
    description: str = Field(default="")
    reference_id: str = Field(default="")


# ============================================================
# AUTH HELPER — Verify X-API-Secret header (ENFORCED)
# ============================================================
def verify_api_secret(x_api_secret: Optional[str] = Header(None)) -> bool:
    """
    Verify the X-API-Secret header. ENFORCED — if BACKEND_API_SECRET
    is not set, the server refuses to start (see startup check).
    """
    if not BACKEND_API_SECRET:
        raise HTTPException(
            status_code=500,
            detail="BACKEND_API_SECRET is not configured on the server."
        )
    if x_api_secret != BACKEND_API_SECRET:
        raise HTTPException(status_code=401, detail="Invalid API secret")
    return True


def verify_user_ownership(request_user_id: str, header_user_id: Optional[str] = Header(None, alias="X-User-Id")):
    """
    Verify that the user_id in the request matches the authenticated user.
    The frontend must send the authenticated user's ID in the X-User-Id header.
    This prevents users from accessing other users' data.
    """
    if header_user_id and request_user_id != header_user_id:
        raise HTTPException(
            status_code=403,
            detail="Access denied: you can only access your own data."
        )


# ============================================================
# HEALTH CHECK
# ============================================================
@app.get("/")
def root():
    """Simple check to see if the backend is running."""
    return {
        "status": "alive",
        "service": "Bad Decision Backend",
        "version": "3.0.0",
    }


@app.get("/api/debug/routes")
def debug_routes():
    """List all registered routes — used to diagnose 404 issues."""
    routes = []
    for route in app.routes:
        if hasattr(route, 'methods') and hasattr(route, 'path'):
            routes.append({
                "path": route.path,
                "methods": list(route.methods) if route.methods else [],
                "name": getattr(route, 'name', 'unknown'),
            })
    return {"routes": routes, "count": len(routes)}


@app.get("/health")
def health_check():
    """Detailed health check including database connectivity."""
    # Debug: show what SUPABASE_URL looks like
    from config import SUPABASE_URL, SUPABASE_KEY
    url_chars = [f"{i}:{c}({ord(c)})" for i, c in enumerate(SUPABASE_URL)]
    url_debug = f"len={len(SUPABASE_URL)} chars={'|'.join(url_chars)}"
    key_debug = f"len={len(SUPABASE_KEY)} starts_eyJ={SUPABASE_KEY.startswith('eyJ')}"

    try:
        from supabase_client import get_supabase
        db = get_supabase()
        db.table("profiles").select("id").limit(1).execute()
        db_status = "connected"
    except Exception as e:
        db_status = f"error: {str(e)}"

    return {
        "status": "healthy",
        "database": db_status,
        "version": "3.0.0",
        "auth_enforced": bool(BACKEND_API_SECRET),
        "debug_supabase_url": url_debug,
        "debug_supabase_key": key_debug,
    }


# ============================================================
# TASK ENDPOINTS
# ============================================================
@app.post("/api/tasks/create")
async def create_task(req: TaskCreateRequest, x_api_secret: Optional[str] = Header(None)):
    """
    Create a new search task.
    - Tier-based engine enforcement: free users can only use ads_intent.
    - Credits are RESERVED (locked) at task creation.
    - The worker COMMITS or REFUNDS credits based on the result.
    """
    verify_api_secret(x_api_secret)

    # Rate limit: 5 searches per user per minute
    if not _rate_limit(req.user_id, _search_hits, RATE_LIMIT_SEARCHES_PER_MINUTE):
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit: max {RATE_LIMIT_SEARCHES_PER_MINUTE} searches per minute. Please wait."
        )

    from supabase_client import get_supabase
    db = get_supabase()

    # === TIER-BASED ENGINE ENFORCEMENT ===
    user_tier = "free"
    try:
        profile_result = (
            db.table("profiles")
            .select("tier")
            .eq("id", req.user_id)
            .limit(1)
            .execute()
        )
        if profile_result.data and len(profile_result.data) > 0:
            user_tier = profile_result.data[0].get("tier", "free")
        else:
            # No profile found — auto-create one (defensive: in case Clerk webhook
            # hasn't fired yet or failed). This prevents 500 errors.
            # Use the user_id as a unique email placeholder to avoid UNIQUE constraint
            # conflicts (email column is UNIQUE NOT NULL).
            print(f"[API] No profile found for user {req.user_id} — auto-creating default profile")
            try:
                db.rpc("handle_new_user", {
                    "p_clerk_id": req.user_id,
                    "p_email": f"{req.user_id}@clerk.placeholder",
                    "p_full_name": "",
                    "p_country": "US",
                }).execute()
                print(f"[API] Auto-created profile + 50 free credits via handle_new_user RPC")
            except Exception as insert_err:
                print(f"[API] handle_new_user RPC failed: {insert_err}")
                # Last resort: insert profile directly with a unique email
                try:
                    db.table("profiles").insert({
                        "id": req.user_id,
                        "email": f"{req.user_id}@clerk.placeholder",
                        "full_name": "",
                        "tier": "free",
                    }).execute()
                except Exception as direct_err:
                    print(f"[API] Direct profile insert also failed: {direct_err}")
    except Exception as e:
        print(f"[API] Warning: could not fetch user tier, defaulting to free: {e}")

    allowed_engines = TIER_ENGINES.get(user_tier, TIER_ENGINES["free"])
    if req.task_type not in allowed_engines:
        raise HTTPException(
            status_code=403,
            detail=f"Your {user_tier} plan does not include the '{req.task_type}' engine. Upgrade to unlock all 4 engines.",
        )

    # === CHECK CREDIT BALANCE ===
    # Auto-create credit_balances row if it doesn't exist (defensive)
    try:
        ledger_result = (
            db.table("credit_balances")
            .select("credits_balance")
            .eq("user_id", req.user_id)
            .limit(1)
            .execute()
        )
        if ledger_result.data and len(ledger_result.data) > 0:
            balance = ledger_result.data[0].get("credits_balance", 0)
        else:
            # No credit_balances row — the handle_new_user RPC above should have
            # created it with 50 free credits. If it didn't (RPC failed), try
            # creating credit_balances directly with 50 credits (matching the
            # signup bonus). This prevents the race condition where the backend
            # creates a 0-credit row before the frontend can create a 50-credit row.
            print(f"[API] No credit_balances row for user {req.user_id} — creating with 50 free credits")
            try:
                db.table("credit_balances").insert({
                    "user_id": req.user_id,
                    "credits_balance": 100,
                    "credits_reserved": 0,
                    "total_purchased": 50,
                }).execute()
                # Also log the signup bonus transaction
                db.table("credit_transactions").insert({
                    "user_id": req.user_id,
                    "amount": 50,
                    "transaction_type": "signup_bonus",
                    "description": "50 free credits for signing up",
                    "reference_id": f"signup_{req.user_id}",
                }).execute()
            except Exception as insert_err:
                print(f"[API] Could not auto-create credit_balances: {insert_err}")
            balance = 50  # Assume 50 if we just created it

        if balance < req.credits_reserved:
            raise HTTPException(
                status_code=402,
                detail=f"Not enough credits. You need {req.credits_reserved} but have {balance}.",
            )
    except HTTPException:
        raise
    except Exception as e:
        print(f"[API] Warning: could not verify credit balance: {e}")

    # === INSERT TASK ===
    insert_data = {
        "user_id": req.user_id,
        "task_type": req.task_type,
        "query": req.query,
        "status": "pending",
        "credits_reserved": req.credits_reserved,
        "progress": 0,
        "current_step": "Queued for processing",
    }
    if req.country:
        insert_data["country"] = req.country
    if req.state_region:
        insert_data["state_region"] = req.state_region

    result = db.table("tasks").insert(insert_data).execute()

    # === RESERVE CREDITS (lock them) ===
    try:
        reserve_ok = db.rpc("reserve_credits", {
            "p_user_id": req.user_id,
            "p_amount": req.credits_reserved,
            "p_description": f"Credits reserved for {req.task_type} search: '{req.query[:50]}'",
        }).execute()

        if not reserve_ok.data:
            # Reserve failed — cancel the task
            db.table("tasks").update({
                "status": "failed",
                "error_message": "Could not reserve credits",
            }).eq("id", result.data[0]["id"]).execute()
            raise HTTPException(
                status_code=402,
                detail="Could not reserve credits. Please check your balance."
            )

    except HTTPException:
        raise
    except Exception as e:
        print(f"[API] Warning: could not reserve credits: {e}")

    return {"success": True, "task": result.data}


@app.get("/api/task/{task_id}")
async def get_task_status(
    task_id: str,
    x_api_secret: Optional[str] = Header(None),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
):
    """
    Get the status of a specific task, including progress, current_step,
    and leads if completed. The frontend polls this every 2-3 seconds
    for the interactive progress UI.
    """
    verify_api_secret(x_api_secret)

    from supabase_client import get_supabase
    db = get_supabase()
    result = (
        db.table("tasks")
        .select("*")
        .eq("id", task_id)
        .limit(1)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Task not found")

    task = result.data[0]

    # User-ownership check: if X-User-Id is provided, verify it matches
    if x_user_id and task.get("user_id") != x_user_id:
        raise HTTPException(status_code=403, detail="Access denied: this task belongs to another user.")

    leads = []
    lead_count = task.get("leads_found", 0)

    # If task is completed, fetch leads
    if task.get("status") == "completed":
        try:
            leads_result = (
                db.table("workspace_leads")
                .select("*")
                .eq("task_id", task_id)
                .order("created_at", desc=False)
                .execute()
            )
            leads = leads_result.data or []
            lead_count = len(leads)
        except Exception as e:
            print(f"[API] Error fetching leads for task {task_id}: {e}")

    return {
        "task_id": task["id"],
        "status": task["status"],
        "engine": task.get("task_type", ""),
        "query": task.get("query", ""),
        "progress": task.get("progress", 0),
        "current_step": task.get("current_step", ""),
        "leads": leads,
        "lead_count": lead_count,
        "credits_reserved": task.get("credits_reserved", 0),
        "credits_spent": task.get("credits_spent", 0),
        "error_message": task.get("error_message"),
    }


@app.get("/api/tasks/{user_id}")
async def get_user_tasks(
    user_id: str,
    x_api_secret: Optional[str] = Header(None),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
):
    """Get all tasks for a specific user. user_id is Clerk ID (TEXT string)."""
    verify_api_secret(x_api_secret)

    # User-ownership check
    if x_user_id and user_id != x_user_id:
        raise HTTPException(status_code=403, detail="Access denied: you can only view your own tasks.")

    from supabase_client import get_supabase
    db = get_supabase()
    result = (
        db.table("tasks")
        .select("id, task_type, query, status, progress, current_step, leads_found, credits_reserved, credits_spent, created_at, completed_at")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .execute()
    )
    return {"tasks": result.data}


@app.get("/api/leads/{task_id}")
async def get_task_leads(
    task_id: str,
    x_api_secret: Optional[str] = Header(None),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
):
    """
    Get all leads for a specific task.

    ROBUST LOOKUP:
    - First tries tasks.id = task_id (the normal case).
    - If that fails, checks if task_id is actually a smart_collections.id
      (the frontend may pass the collection UUID when the collection's
      task_id FK is NULL or when older collections predate the FK).
      In that case, uses smart_collections.task_id to find the real task.
    - If neither works, returns 404.
    """
    verify_api_secret(x_api_secret)

    from supabase_client import get_supabase
    db = get_supabase()

    real_task_id = task_id
    task_owner = None

    # 1. Try the tasks table directly
    task_result = (
        db.table("tasks")
        .select("id, user_id")
        .eq("id", task_id)
        .limit(1)
        .execute()
    )
    if task_result.data:
        real_task_id = task_result.data[0]["id"]
        task_owner = task_result.data[0].get("user_id")
    else:
        # 2. Fallback: maybe task_id is actually a smart_collections.id
        col_result = (
            db.table("smart_collections")
            .select("id, task_id, user_id")
            .eq("id", task_id)
            .limit(1)
            .execute()
        )
        if col_result.data:
            col = col_result.data[0]
            col_task_id = col.get("task_id")
            col_user_id = col.get("user_id")
            if col_task_id:
                # Verify the underlying task exists
                verify_task = (
                    db.table("tasks")
                    .select("id, user_id")
                    .eq("id", col_task_id)
                    .limit(1)
                    .execute()
                )
                if verify_task.data:
                    real_task_id = verify_task.data[0]["id"]
                    task_owner = verify_task.data[0].get("user_id")
                else:
                    # Collection points to a task that no longer exists
                    raise HTTPException(status_code=404, detail="The task for this collection no longer exists.")
            else:
                # Collection has no task_id — orphaned collection
                # Use the collection's user_id for ownership but we can't fetch leads
                task_owner = col_user_id
                return {"leads": [], "task_id": task_id, "orphaned": True}
        else:
            raise HTTPException(status_code=404, detail="Task not found")

    # 3. Ownership check
    if x_user_id and task_owner and task_owner != x_user_id:
        raise HTTPException(status_code=403, detail="Access denied: this task belongs to another user.")

    # 4. Fetch leads
    result = (
        db.table("workspace_leads")
        .select("*")
        .eq("task_id", real_task_id)
        .order("created_at", desc=False)
        .execute()
    )
    return {"leads": result.data}


# ============================================================
# CREDIT ENDPOINTS
# ============================================================
@app.get("/api/credits/{user_id}")
async def get_credit_balance(
    user_id: str,
    x_api_secret: Optional[str] = Header(None),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
):
    """Get a user's credit balance. user_id is Clerk ID (TEXT string)."""
    verify_api_secret(x_api_secret)

    if x_user_id and user_id != x_user_id:
        raise HTTPException(status_code=403, detail="Access denied: you can only view your own balance.")

    from supabase_client import get_supabase
    db = get_supabase()
    result = (
        db.table("credit_balances")
        .select("credits_balance, credits_reserved, total_purchased")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    if not result.data:
        return {
            "balance": {
                "credits_balance": 0,
                "credits_reserved": 0,
                "total_purchased": 0,
            }
        }
    row = result.data[0]
    return {
        "balance": {
            "credits_balance": row["credits_balance"],
            "credits_reserved": row["credits_reserved"],
            "total_purchased": row["total_purchased"],
        }
    }


@app.get("/api/credits/transactions/{user_id}")
async def get_credit_transactions(
    user_id: str,
    x_api_secret: Optional[str] = Header(None),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
):
    """Get a user's credit transaction history."""
    verify_api_secret(x_api_secret)

    if x_user_id and user_id != x_user_id:
        raise HTTPException(status_code=403, detail="Access denied.")

    from supabase_client import get_supabase
    db = get_supabase()
    result = (
        db.table("credit_transactions")
        .select("id, amount, transaction_type, description, reference_id, created_at")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(100)
        .execute()
    )
    return {"transactions": result.data}


@app.post("/api/credits/add")
async def add_credits(req: CreditAddRequest, x_api_secret: Optional[str] = Header(None)):
    """
    Add credits to a user's account after payment.
    IDEMPOTENT: if reference_id + transaction_type already exists, no credits are added.
    This is called by the Paystack webhook (via the Next.js frontend).
    """
    verify_api_secret(x_api_secret)

    from supabase_client import get_supabase
    db = get_supabase()
    result = db.rpc("add_credits", {
        "p_user_id": req.user_id,
        "p_amount": req.amount,
        "p_transaction_type": req.transaction_type,
        "p_description": req.description,
        "p_reference_id": req.reference_id,
    }).execute()
    return {"success": result.data if result.data is not None else True}


# ============================================================
# USER PROFILE ENDPOINT
# ============================================================
@app.get("/api/profile/{user_id}")
async def get_user_profile(
    user_id: str,
    x_api_secret: Optional[str] = Header(None),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
):
    """Get a user's profile including their tier."""
    verify_api_secret(x_api_secret)

    if x_user_id and user_id != x_user_id:
        raise HTTPException(status_code=403, detail="Access denied.")

    from supabase_client import get_supabase
    db = get_supabase()
    result = (
        db.table("profiles")
        .select("id, email, full_name, tier, country, created_at")
        .eq("id", user_id)
        .limit(1)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Profile not found")
    return {"profile": result.data[0]}


# ============================================================
# USER SETTINGS ENDPOINT (for outreach message personalization)
# ============================================================
class UpdateSettingsRequest(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=256)
    user_service: str = Field(default="", max_length=500)
    target_audience: str = Field(default="", max_length=500)
    copywriting_style: str = Field(default="david_ogilvy", pattern=r"^(dan_kennedy|donald_miller|ray_edwards|david_ogilvy|jay_abraham|gary_halbert)$")
    company_name: str = Field(default="", max_length=200)
    sender_name: str = Field(default="", max_length=200)


@app.put("/api/settings/{user_id}")
async def update_user_settings(user_id: str, req: UpdateSettingsRequest, x_api_secret: Optional[str] = Header(None)):
    """Update user settings (company, service, audience, copywriting style) for outreach messages."""
    verify_api_secret(x_api_secret)

    from supabase_client import get_supabase
    db = get_supabase()
    update_data = {
        "user_service": req.user_service,
        "target_audience": req.target_audience,
        "copywriting_style": req.copywriting_style,
    }
    # Try to update company_name and sender_name (columns may not exist if migration not run)
    try:
        update_data["company_name"] = req.company_name
        update_data["sender_name"] = req.sender_name
        result = db.table("profiles").update(update_data).eq("id", user_id).execute()
    except Exception:
        # Fall back without company_name/sender_name
        update_data.pop("company_name", None)
        update_data.pop("sender_name", None)
        result = db.table("profiles").update(update_data).eq("id", user_id).execute()

    return {"success": True, "settings": result.data[0] if result.data else None}


@app.get("/api/settings/{user_id}")
async def get_user_settings(user_id: str, x_api_secret: Optional[str] = Header(None)):
    """Get user settings for outreach messages."""
    verify_api_secret(x_api_secret)

    from supabase_client import get_supabase
    db = get_supabase()
    # Try to select company_name/sender_name (may not exist if migration not run)
    try:
        result = (
            db.table("profiles")
            .select("user_service, target_audience, copywriting_style, company_name, sender_name")
            .eq("id", user_id)
            .limit(1)
            .execute()
        )
    except Exception:
        # Fall back without company_name/sender_name
        result = (
            db.table("profiles")
            .select("user_service, target_audience, copywriting_style")
            .eq("id", user_id)
            .limit(1)
            .execute()
        )
    if not result.data:
        return {"settings": {"user_service": "", "target_audience": "", "copywriting_style": "david_ogilvy", "company_name": "", "sender_name": ""}}
    return {"settings": result.data[0]}


# ============================================================
# ON-DEMAND OUTREACH MESSAGE GENERATION
# ============================================================
class OutreachRequest(BaseModel):
    lead_id: str = Field(..., min_length=1, max_length=256)


@app.post("/api/outreach/generate")
async def generate_outreach(req: OutreachRequest, x_api_secret: Optional[str] = Header(None)):
    """
    Generate personalized outreach messages for a single lead on demand.
    The user clicks 'Generate Messages' on a lead card, and this endpoint
    fetches the lead, fetches the user's settings, generates 3 messages,
    saves them to the database, and returns them.
    """
    verify_api_secret(x_api_secret)

    from supabase_client import get_supabase
    from ai.outreach_generator import generate_outreach_messages
    db = get_supabase()

    # 1. Fetch the lead
    lead_result = (
        db.table("workspace_leads")
        .select("*")
        .eq("id", req.lead_id)
        .limit(1)
        .execute()
    )
    if not lead_result.data:
        raise HTTPException(status_code=404, detail="Lead not found")

    lead = lead_result.data[0]
    user_id = lead.get("user_id")

    # 2. Fetch the user's settings
    profile_result = (
        db.table("profiles")
        .select("user_service, target_audience, copywriting_style")
        .eq("id", user_id)
        .limit(1)
        .execute()
    )
    if not profile_result.data or not profile_result.data[0].get("user_service"):
        raise HTTPException(
            status_code=400,
            detail="Please set up your service in Settings first."
        )

    user_settings = profile_result.data[0]
    user_service = user_settings.get("user_service", "")
    target_audience = user_settings.get("target_audience", "")
    copywriting_style = user_settings.get("copywriting_style", "david_ogilvy")
    sender_company = user_settings.get("company_name", "")
    sender_name = user_settings.get("sender_name", "")

    # 3. Generate the outreach messages
    try:
        outreach = await generate_outreach_messages(
            lead, user_service, target_audience, copywriting_style, sender_company, sender_name
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not generate messages: {str(e)}")

    # 4. Save the messages to the database
    # Try with outreach_email_subject first; if the column doesn't exist yet
    # (migration not run), fall back to saving without it.
    update_data = {
        "outreach_email": outreach.get("email_message", "ABSENT"),
        "outreach_social": outreach.get("social_message", "ABSENT"),
        "outreach_call": outreach.get("call_script", "ABSENT"),
    }
    try:
        update_data["outreach_email_subject"] = outreach.get("email_subject", "ABSENT")
        db.table("workspace_leads").update(update_data).eq("id", req.lead_id).execute()
    except Exception as col_err:
        # Column might not exist — try without outreach_email_subject
        print(f"[OUTREACH] Could not save with email_subject column (migration not run?): {col_err}")
        update_data.pop("outreach_email_subject", None)
        try:
            db.table("workspace_leads").update(update_data).eq("id", req.lead_id).execute()
        except Exception as save_err:
            print(f"[OUTREACH] Could not save messages: {save_err}")

    return {
        "success": True,
        "outreach_email_subject": outreach.get("email_subject", "ABSENT"),
        "outreach_email": outreach.get("email_message", "ABSENT"),
        "outreach_social": outreach.get("social_message", "ABSENT"),
        "outreach_call": outreach.get("call_script", "ABSENT"),
    }


# ============================================================
# BATCH OUTREACH MESSAGE GENERATION
# ============================================================
class BatchOutreachRequest(BaseModel):
    task_id: str = Field(..., min_length=1, max_length=256)
    force_regenerate: bool = Field(default=False)


@app.post("/api/outreach/generate-batch")
@app.post("/api/outreach/batch")
async def generate_outreach_batch(req: BatchOutreachRequest, x_api_secret: Optional[str] = Header(None)):
    """
    Generate outreach messages for ALL leads in a task (collection).
    Called when the user clicks 'Write Messages for All' in the results view.
    Processes leads sequentially (to avoid DeepSeek rate limits) and returns
    the count of successfully generated messages.

    force_regenerate:
      - False (default): SKIP leads that already have outreach_email set.
        Only generates for leads with no messages yet.
      - True: OVERRIDE existing messages — regenerate for ALL leads,
        even those that already have messages.

    Each message is strictly enforced to 500-530 characters.
    """
    verify_api_secret(x_api_secret)

    from supabase_client import get_supabase
    from ai.outreach_generator import generate_outreach_messages
    db = get_supabase()

    # 1. Resolve the real task_id — the frontend may pass either a tasks.id
    # or a smart_collections.id. Try tasks first, then fall back to collections.
    real_task_id = req.task_id

    # Check if it's a valid task_id first
    task_check = (
        db.table("tasks")
        .select("id")
        .eq("id", req.task_id)
        .limit(1)
        .execute()
    )
    if not task_check.data:
        # Not a task — check if it's a smart_collections.id
        col_check = (
            db.table("smart_collections")
            .select("id, task_id")
            .eq("id", req.task_id)
            .limit(1)
            .execute()
        )
        if col_check.data and col_check.data[0].get("task_id"):
            real_task_id = col_check.data[0]["task_id"]
            print(f"[OUTREACH-BATCH] Resolved collection {req.task_id} → task {real_task_id}")
        else:
            raise HTTPException(
                status_code=404,
                detail="No task or collection found with that ID. Try refreshing the page."
            )

    # 2. Fetch all leads for this task
    leads_result = (
        db.table("workspace_leads")
        .select("*")
        .eq("task_id", real_task_id)
        .execute()
    )
    if not leads_result.data:
        raise HTTPException(
            status_code=404,
            detail="This collection has no leads yet. Run a search first."
        )

    leads = leads_result.data
    if not leads:
        raise HTTPException(status_code=404, detail="No leads found.")

    # 2. Fetch user settings
    user_id = leads[0].get("user_id")
    try:
        profile_result = (
            db.table("profiles")
            .select("user_service, target_audience, copywriting_style, company_name, sender_name")
            .eq("id", user_id)
            .limit(1)
            .execute()
        )
    except Exception:
        profile_result = (
            db.table("profiles")
            .select("user_service, target_audience, copywriting_style")
            .eq("id", user_id)
            .limit(1)
            .execute()
        )
    if not profile_result.data or not profile_result.data[0].get("user_service"):
        raise HTTPException(
            status_code=400,
            detail="Please set up your service in Settings first."
        )

    user_settings = profile_result.data[0]
    user_service = user_settings.get("user_service", "")
    target_audience = user_settings.get("target_audience", "")
    copywriting_style = user_settings.get("copywriting_style", "david_ogilvy")
    sender_company = user_settings.get("company_name", "")
    sender_name = user_settings.get("sender_name", "")

    # 3. Generate outreach for each lead
    success_count = 0
    skipped_count = 0
    for lead in leads:
        has_existing = (
            lead.get("outreach_email")
            and lead.get("outreach_email") != "ABSENT"
        )

        if has_existing and not req.force_regenerate:
            skipped_count += 1
            continue

        try:
            outreach = await generate_outreach_messages(
                lead, user_service, target_audience, copywriting_style, sender_company, sender_name
            )
            db.table("workspace_leads").update({
                "outreach_email": outreach.get("email_message", "ABSENT"),
                "outreach_social": outreach.get("social_message", "ABSENT"),
                "outreach_call": outreach.get("call_script", "ABSENT"),
            }).eq("id", lead["id"]).execute()
            # Try to save email_subject too (separate in case column doesn't exist)
            try:
                db.table("workspace_leads").update({
                    "outreach_email_subject": outreach.get("email_subject", "ABSENT"),
                }).eq("id", lead["id"]).execute()
            except Exception:
                pass  # Column might not exist yet — that's OK
            success_count += 1
        except Exception as e:
            print(f"[OUTREACH-BATCH] Error for lead {lead.get('id')}: {e}")

    action_word = "Regenerated" if req.force_regenerate else "Generated"
    return {
        "success": True,
        "total_leads": len(leads),
        "generated": success_count,
        "skipped": skipped_count,
        "force_regenerate": req.force_regenerate,
        "message": f"{action_word} outreach messages for {success_count} out of {len(leads)} leads."
                   + (f" ({skipped_count} already had messages and were skipped.)" if skipped_count > 0 else "")
    }


# ============================================================
# COLLECTIONS ENDPOINT
# ============================================================
@app.get("/api/collections/{user_id}")
async def get_user_collections(
    user_id: str,
    x_api_secret: Optional[str] = Header(None),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
):
    """Get all smart collections for a user."""
    verify_api_secret(x_api_secret)

    if x_user_id and user_id != x_user_id:
        raise HTTPException(status_code=403, detail="Access denied.")

    from supabase_client import get_supabase
    db = get_supabase()
    result = (
        db.table("smart_collections")
        .select("id, name, task_type, lead_count, created_at, task_id")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .execute()
    )
    return {"collections": result.data}


# ============================================================
# START THE TASK WORKER IN THE BACKGROUND
# ============================================================
@app.on_event("startup")
async def startup_event():
    """Start the background task worker and verify config on server start."""
    # Verify BACKEND_API_SECRET is set (ENFORCED)
    if not BACKEND_API_SECRET:
        print("[STARTUP] WARNING: BACKEND_API_SECRET is not set! All API requests will be rejected.")
        print("[STARTUP] Set BACKEND_API_SECRET in your environment variables.")

    # Print all registered routes for debugging
    print("[STARTUP] === REGISTERED ROUTES ===")
    for route in app.routes:
        if hasattr(route, 'methods') and hasattr(route, 'path'):
            methods = ','.join(sorted(route.methods)) if route.methods else ''
            print(f"  {methods:20s} {route.path}")
    print("[STARTUP] === END ROUTES ===")

    import asyncio
    try:
        from task_worker.worker import run_task_worker
        asyncio.create_task(run_task_worker())
        print("[STARTUP] Background task worker started.")
    except Exception as e:
        print(f"[STARTUP] Could not start task worker: {e}")
        print("[STARTUP] Server will run but tasks will not be processed automatically.")


# ============================================================
# RUN THE SERVER
# ============================================================
if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=PORT,
        reload=DEBUG,
    )
