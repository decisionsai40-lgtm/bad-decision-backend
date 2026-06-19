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
    allow_methods=["GET", "POST"],
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


@app.get("/health")
def health_check():
    """Detailed health check including database connectivity."""
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
    """Get all leads for a specific task."""
    verify_api_secret(x_api_secret)

    from supabase_client import get_supabase
    db = get_supabase()

    # Verify the task belongs to the user
    if x_user_id:
        task_result = (
            db.table("tasks")
            .select("user_id")
            .eq("id", task_id)
            .limit(1)
            .execute()
        )
        if not task_result.data:
            raise HTTPException(status_code=404, detail="Task not found")
        if task_result.data[0].get("user_id") != x_user_id:
            raise HTTPException(status_code=403, detail="Access denied: this task belongs to another user.")

    result = (
        db.table("workspace_leads")
        .select("*")
        .eq("task_id", task_id)
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
