"""
BAD DECISION AI — FastAPI Main Server
======================================
This is the entry point for the entire Python backend.
It starts the web server and sets up all the routes.

IMPORTANT: Clerk user IDs (like "user_3EpAbGzlWhXf8l8H1clTpAxaDY0")
are TEXT, not UUIDs. All user_id columns are TEXT type.

TABLE NAMES: The actual Supabase tables are:
  - profiles (id is TEXT — stores Clerk ID directly)
  - coin_balances (NOT usage_ledger!)
  - coin_transactions
  - tasks
  - global_intelligence_cache
  - workspace_leads
  - smart_collections

COIN BALANCES COLUMNS:
  - user_id TEXT
  - balance INTEGER (NOT coins_balance!)
  - coins_reserved INTEGER
  - total_purchased INTEGER (NOT coins_lifetime!)
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from config import PORT, DEBUG, COIN_FREE_TRIAL

# Create the FastAPI app
app = FastAPI(
    title="Bad Decision AI — Backend Engine",
    description="The scraping and validation engine that powers Bad Decision AI",
    version="1.3.0",
)

# ============================================================
# CORS — Allow the Next.js frontend to talk to this backend
# ============================================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# HELPER — Get or create a profile for a Clerk user
# ============================================================
def _ensure_profile(db, user_id: str, email: str = "") -> dict:
    """
    Ensure the user has a profile and coin_balances row.
    Called on first search or when the Clerk webhook hasn't fired yet.
    This is a safety net — the Clerk webhook is the primary path.
    """
    # Check if profile exists
    result = db.table("profiles").select("id, tier, email").eq("id", user_id).execute()

    if result.data and len(result.data) > 0:
        return result.data[0]

    # Profile doesn't exist — create it (fallback if webhook missed)
    profile_data = {
        "id": user_id,  # TEXT column — Clerk IDs work directly
        "tier": "free",
        "email": email,
        "full_name": "",
    }

    profile_result = db.table("profiles").insert(profile_data).execute()

    # Also create the coin_balances row with free trial coins
    try:
        db.table("coin_balances").insert({
            "user_id": user_id,
            "balance": COIN_FREE_TRIAL,        # NOT coins_balance!
            "coins_reserved": 0,
            "total_purchased": COIN_FREE_TRIAL, # NOT coins_lifetime!
        }).execute()
    except Exception as e:
        print(f"[BACKEND] Coin balance creation error (may already exist): {e}")

    return profile_result.data[0] if profile_result.data else profile_data


# ============================================================
# HEALTH CHECK — Is the server alive?
# ============================================================
@app.get("/")
def root():
    """Simple check to see if the backend is running."""
    return {
        "status": "alive",
        "service": "Bad Decision AI Backend",
        "version": "1.3.0",
    }


@app.get("/health")
def health_check():
    """More detailed health check."""
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
    }


# ============================================================
# PROFILE ENDPOINTS
# ============================================================
@app.get("/api/profile/{user_id}")
async def get_profile(user_id: str):
    """Get a user's profile by their Clerk ID."""
    from supabase_client import get_supabase
    db = get_supabase()

    profile = _ensure_profile(db, user_id)

    # Also get their coin balance from coin_balances (NOT usage_ledger!)
    ledger_result = db.table("coin_balances").select("*").eq("user_id", user_id).execute()
    raw_ledger = ledger_result.data[0] if ledger_result.data else {
        "user_id": user_id,
        "balance": COIN_FREE_TRIAL,
        "coins_reserved": 0,
        "total_purchased": COIN_FREE_TRIAL,
    }

    # Map database column names to what the frontend expects
    # DB: balance → Frontend: coins_balance
    # DB: total_purchased → Frontend: coins_lifetime
    ledger = {
        "user_id": raw_ledger.get("user_id", user_id),
        "coins_balance": raw_ledger.get("balance", COIN_FREE_TRIAL),
        "coins_reserved": raw_ledger.get("coins_reserved", 0),
        "coins_lifetime": raw_ledger.get("total_purchased", COIN_FREE_TRIAL),
    }

    return {
        "profile": profile,
        "ledger": ledger,
    }


# ============================================================
# TASK ENDPOINTS
# ============================================================
@app.post("/api/tasks/create")
async def create_task(
    user_id: str,
    task_type: str,
    query: str,
    coins_reserved: int = 0,
):
    """Create a new search task. user_id is the Clerk ID (TEXT)."""
    from supabase_client import get_supabase
    db = get_supabase()

    # Ensure the user has a profile (safety net)
    _ensure_profile(db, user_id)

    # Check coin balance before creating task
    ledger_result = db.table("coin_balances").select("balance").eq("user_id", user_id).execute()
    if ledger_result.data:
        balance = ledger_result.data[0].get("balance", 0)
        if balance <= 0:
            raise HTTPException(status_code=402, detail="Insufficient coins. Please top up your balance.")

    result = db.table("tasks").insert({
        "user_id": user_id,  # TEXT column — Clerk IDs work directly
        "task_type": task_type,
        "query": query,
        "status": "pending",
        "coins_reserved": coins_reserved,
    }).execute()
    return {"success": True, "task": result.data}


@app.get("/api/tasks/{user_id}")
async def get_user_tasks(user_id: str):
    """Get all tasks for a specific user. user_id is the Clerk ID (TEXT)."""
    from supabase_client import get_supabase
    db = get_supabase()
    result = (
        db.table("tasks")
        .select("*")
        .eq("user_id", user_id)  # TEXT column — works with Clerk IDs
        .order("created_at", desc=True)
        .execute()
    )
    return {"tasks": result.data}


# ============================================================
# LEADS ENDPOINT — Fixed to support both task_id and collection_id
# ============================================================
@app.get("/api/leads/{lookup_id}")
async def get_collection_leads(lookup_id: str):
    """
    Get all leads for a search result.
    The frontend may pass either a collection_id OR a task_id.
    We try collection_id first (proper way), then fall back to task_id.
    """
    from supabase_client import get_supabase
    db = get_supabase()

    # Try 1: Look up by collection_id (the proper way)
    result = (
        db.table("workspace_leads")
        .select("*, global_intelligence_cache(*)")
        .eq("collection_id", lookup_id)
        .execute()
    )

    if result.data and len(result.data) > 0:
        return {"leads": result.data}

    # Try 2: Look up by task_id (frontend compatibility — it passes task IDs)
    result = (
        db.table("workspace_leads")
        .select("*, global_intelligence_cache(*)")
        .eq("task_id", lookup_id)
        .execute()
    )

    return {"leads": result.data or []}


# ============================================================
# COLLECTIONS ENDPOINT — New endpoint for proper collection listing
# ============================================================
@app.get("/api/collections/{user_id}")
async def get_user_collections(user_id: str):
    """
    Get all smart collections for a user, with lead counts.
    This is the proper way for the frontend to list past searches.
    """
    from supabase_client import get_supabase
    db = get_supabase()

    result = (
        db.table("smart_collections")
        .select("id, user_id, name, task_type, created_at, workspace_leads(count)")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .execute()
    )

    collections = []
    for col in (result.data or []):
        lead_count = 0
        wl = col.get("workspace_leads")
        if isinstance(wl, list) and len(wl) > 0:
            lead_count = wl[0].get("count", 0) if isinstance(wl[0], dict) else len(wl)
        elif isinstance(wl, dict):
            lead_count = wl.get("count", 0)

        collections.append({
            "id": col.get("id"),
            "name": col.get("name", "Untitled Search"),
            "task_type": col.get("task_type", ""),
            "lead_count": lead_count,
            "created_at": col.get("created_at", ""),
        })

    return {"collections": collections}


@app.get("/api/cache/check")
async def check_cache(company_name: str = "", website_url: str = ""):
    """Check the global cache for existing leads."""
    from supabase_client import get_supabase
    db = get_supabase()
    try:
        result = db.rpc("check_global_cache", {
            "p_company_name": company_name,
            "p_website_url": website_url,
        }).execute()
        return {"cache_hits": result.data}
    except Exception as e:
        return {"cache_hits": [], "error": str(e)}


# ============================================================
# COIN ENDPOINTS
# ============================================================
@app.post("/api/coins/deduct")
async def deduct_coins(user_id: str, amount: int):
    """Deduct coins from a user's ledger. user_id is the Clerk ID (TEXT)."""
    from supabase_client import get_supabase
    db = get_supabase()
    try:
        result = db.rpc("deduct_coins", {
            "p_user_id": user_id,
            "p_amount": amount,
        }).execute()
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/coins/add")
async def add_coins(user_id: str, amount: int):
    """Add coins to a user's ledger after payment. user_id is the Clerk ID (TEXT)."""
    from supabase_client import get_supabase
    db = get_supabase()

    # Use the add_coins RPC — it handles creating the ledger row if missing
    try:
        db.rpc("add_coins", {
            "p_user_id": user_id,
            "p_amount": amount,
        }).execute()
    except Exception as e:
        print(f"[BACKEND] add_coins RPC error: {e}")
        # Fallback: manual increment if RPC fails
        ledger_result = db.table("coin_balances").select("balance, total_purchased").eq("user_id", user_id).execute()

        if not ledger_result.data:
            # No row exists — create it
            db.table("coin_balances").insert({
                "user_id": user_id,
                "balance": amount,
                "coins_reserved": 0,
                "total_purchased": amount,
            }).execute()
        else:
            # Row exists — INCREMENT (not overwrite!)
            current_balance = ledger_result.data[0].get("balance", 0)
            current_purchased = ledger_result.data[0].get("total_purchased", 0)
            db.table("coin_balances").update({
                "balance": current_balance + amount,
                "total_purchased": current_purchased + amount,
            }).eq("user_id", user_id).execute()

    return {"success": True}


@app.get("/api/coins/{user_id}")
async def get_coin_balance(user_id: str):
    """Get a user's coin balance. user_id is the Clerk ID (TEXT)."""
    from supabase_client import get_supabase
    db = get_supabase()
    result = db.table("coin_balances").select("*").eq("user_id", user_id).execute()

    if result.data:
        raw = result.data[0]
        # Map database column names to what the frontend expects
        balance = {
            "user_id": raw.get("user_id", user_id),
            "coins_balance": raw.get("balance", COIN_FREE_TRIAL),
            "coins_reserved": raw.get("coins_reserved", 0),
            "coins_lifetime": raw.get("total_purchased", COIN_FREE_TRIAL),
        }
        return {"balance": balance}

    # No ledger found — return default free trial balance
    return {"balance": {
        "user_id": user_id,
        "coins_balance": COIN_FREE_TRIAL,
        "coins_reserved": 0,
        "coins_lifetime": COIN_FREE_TRIAL,
    }}


# ============================================================
# START THE TASK WORKER IN THE BACKGROUND
# ============================================================
@app.on_event("startup")
async def startup_event():
    """Start the background task worker when server starts."""
    import asyncio
    from task_worker.worker import run_task_worker
    asyncio.create_task(run_task_worker())


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
