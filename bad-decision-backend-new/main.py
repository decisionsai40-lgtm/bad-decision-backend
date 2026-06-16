"""
BAD DECISION AI — FastAPI Main Server
======================================
Entry point for the entire Python backend.
Starts the web server and sets up all the routes.

KEY DESIGN:
- Clerk user IDs are TEXT strings like "user_3Ew45fAIqwEJ3naNttXUPMxTfFt", NOT UUIDs.
- Uses Pydantic models for JSON body parsing (fixes 422 errors).
- Tier-based engine enforcement: free users can only use ads_intent and smb_maps.
- Coin balance endpoint at /api/coins/{user_id}.
- Background task worker started on startup.
- No device fingerprint logic — multiple accounts per device are allowed.
"""

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional
import uvicorn
import os
from config import PORT, DEBUG, BACKEND_API_SECRET

# Create the FastAPI app
app = FastAPI(
    title="Bad Decision AI — Backend Engine",
    description="The scraping and validation engine that powers Bad Decision AI",
    version="2.0.0",
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
# TIER CONFIG — which engines each tier can use
# ============================================================
TIER_ENGINES = {
    "free":    ["ads_intent", "smb_maps"],
    "starter": ["ads_intent", "smb_maps", "web_absent", "social_intent"],
    "growth":  ["ads_intent", "smb_maps", "web_absent", "social_intent"],
    "pro":     ["ads_intent", "smb_maps", "web_absent", "social_intent"],
}

VALID_ENGINES = {"ads_intent", "smb_maps", "web_absent", "social_intent"}


# ============================================================
# PYDANTIC MODELS
# ============================================================
class TaskCreateRequest(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=256)
    task_type: str = Field(..., pattern=r"^(ads_intent|smb_maps|web_absent|social_intent)$")
    query: str = Field(..., min_length=1, max_length=1000)
    coins_reserved: int = Field(default=2, ge=0)
    country: str = Field(default="", max_length=10)
    state_region: str = Field(default="", max_length=100)


class CoinOperationRequest(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=256)
    amount: int = Field(..., gt=0, le=100000)


# ============================================================
# AUTH HELPER — Verify X-API-Secret header (optional)
# ============================================================
def verify_api_secret(x_api_secret: Optional[str] = Header(None)) -> bool:
    """
    Verify the X-API-Secret header if BACKEND_API_SECRET is set.
    If BACKEND_API_SECRET is empty or unset, allow all requests (dev mode).
    """
    if not BACKEND_API_SECRET:
        return True
    return x_api_secret == BACKEND_API_SECRET


# ============================================================
# HEALTH CHECK
# ============================================================
@app.get("/")
def root():
    """Simple check to see if the backend is running."""
    return {
        "status": "alive",
        "service": "Bad Decision AI Backend",
        "version": "2.0.0",
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
        "version": "2.0.0",
    }


# ============================================================
# TASK ENDPOINTS
# ============================================================
@app.post("/api/tasks/create")
async def create_task(req: TaskCreateRequest, x_api_secret: Optional[str] = Header(None)):
    """
    Create a new search task.
    Tier-based engine enforcement: free users can only use ads_intent and smb_maps.
    """
    if not verify_api_secret(x_api_secret):
        raise HTTPException(status_code=401, detail="Invalid API secret")

    from supabase_client import get_supabase
    db = get_supabase()

    # === TIER-BASED ENGINE ENFORCEMENT ===
    # Fetch user's tier from profiles table
    user_tier = "free"  # default
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
    except Exception as e:
        print(f"[API] Warning: could not fetch user tier, defaulting to free: {e}")

    allowed_engines = TIER_ENGINES.get(user_tier, TIER_ENGINES["free"])
    if req.task_type not in allowed_engines:
        raise HTTPException(
            status_code=403,
            detail=f"Your {user_tier} plan does not include the '{req.task_type}' engine. Upgrade to unlock all 4 engines.",
        )

    # === CHECK COIN BALANCE ===
    try:
        ledger_result = (
            db.table("usage_ledger")
            .select("coins_balance")
            .eq("user_id", req.user_id)
            .limit(1)
            .execute()
        )
        if ledger_result.data and len(ledger_result.data) > 0:
            balance = ledger_result.data[0].get("coins_balance", 0)
            if balance < req.coins_reserved:
                raise HTTPException(
                    status_code=402,
                    detail=f"Not enough coins. You need {req.coins_reserved} but have {balance}.",
                )
    except HTTPException:
        raise
    except Exception as e:
        print(f"[API] Warning: could not verify coin balance: {e}")

    # === INSERT TASK ===
    insert_data = {
        "user_id": req.user_id,
        "task_type": req.task_type,
        "query": req.query,
        "status": "pending",
        "coins_reserved": req.coins_reserved,
    }
    if req.country:
        insert_data["country"] = req.country
    if req.state_region:
        insert_data["state_region"] = req.state_region

    result = db.table("tasks").insert(insert_data).execute()

    # === DEDUCT COINS (reserve them) ===
    try:
        db.rpc("deduct_coins", {
            "p_user_id": req.user_id,
            "p_amount": req.coins_reserved,
        }).execute()
    except Exception as e:
        print(f"[API] Warning: could not deduct coins: {e}")

    return {"success": True, "task": result.data}


@app.get("/api/task/{task_id}")
async def get_task_status(task_id: str, x_api_secret: Optional[str] = Header(None)):
    """Get the status of a specific task by its ID, including leads if completed."""
    if not verify_api_secret(x_api_secret):
        raise HTTPException(status_code=401, detail="Invalid API secret")

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
    leads = []

    # If task is completed, also fetch leads
    if task.get("status") == "completed":
        try:
            coll_result = (
                db.table("smart_collections")
                .select("id")
                .eq("task_id", task_id)
                .limit(1)
                .execute()
            )
            if coll_result.data:
                collection_id = coll_result.data[0]["id"]
                leads_result = (
                    db.table("workspace_leads")
                    .select("*, global_intelligence_cache(*)")
                    .eq("collection_id", collection_id)
                    .execute()
                )
                leads = leads_result.data or []
        except Exception as e:
            print(f"[API] Error fetching leads for task {task_id}: {e}")

    return {
        "task_id": task["id"],
        "status": task["status"],
        "engine": task.get("task_type", ""),
        "query": task.get("query", ""),
        "leads": leads,
        "lead_count": len(leads),
    }


@app.get("/api/tasks/{user_id}")
async def get_user_tasks(user_id: str, x_api_secret: Optional[str] = Header(None)):
    """Get all tasks for a specific user. user_id is Clerk ID (TEXT string)."""
    if not verify_api_secret(x_api_secret):
        raise HTTPException(status_code=401, detail="Invalid API secret")

    from supabase_client import get_supabase
    db = get_supabase()
    result = (
        db.table("tasks")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .execute()
    )
    return {"tasks": result.data}


@app.get("/api/leads/{collection_id}")
async def get_collection_leads(collection_id: str, x_api_secret: Optional[str] = Header(None)):
    """Get all leads in a specific Smart Collection."""
    if not verify_api_secret(x_api_secret):
        raise HTTPException(status_code=401, detail="Invalid API secret")

    from supabase_client import get_supabase
    db = get_supabase()
    result = (
        db.table("workspace_leads")
        .select("*, global_intelligence_cache(*)")
        .eq("collection_id", collection_id)
        .execute()
    )
    return {"leads": result.data}


# ============================================================
# COIN ENDPOINTS
# ============================================================
@app.get("/api/coins/{user_id}")
async def get_coin_balance(user_id: str, x_api_secret: Optional[str] = Header(None)):
    """Get a user's coin balance. user_id is Clerk ID (TEXT string)."""
    if not verify_api_secret(x_api_secret):
        raise HTTPException(status_code=401, detail="Invalid API secret")

    from supabase_client import get_supabase
    db = get_supabase()
    result = (
        db.table("usage_ledger")
        .select("coins_balance, coins_reserved, coins_lifetime")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    if not result.data:
        return {
            "balance": {
                "coins_balance": 0,
                "coins_reserved": 0,
                "coins_lifetime": 0,
            }
        }
    row = result.data[0]
    return {
        "balance": {
            "coins_balance": row["coins_balance"],
            "coins_reserved": row["coins_reserved"],
            "coins_lifetime": row["coins_lifetime"],
        }
    }


@app.post("/api/coins/deduct")
async def deduct_coins(req: CoinOperationRequest, x_api_secret: Optional[str] = Header(None)):
    """Deduct coins from a user's ledger."""
    if not verify_api_secret(x_api_secret):
        raise HTTPException(status_code=401, detail="Invalid API secret")

    from supabase_client import get_supabase
    db = get_supabase()
    result = db.rpc("deduct_coins", {
        "p_user_id": req.user_id,
        "p_amount": req.amount,
    }).execute()
    return {"success": result.data}


@app.post("/api/coins/add")
async def add_coins(req: CoinOperationRequest, x_api_secret: Optional[str] = Header(None)):
    """Add coins to a user's ledger after payment."""
    if not verify_api_secret(x_api_secret):
        raise HTTPException(status_code=401, detail="Invalid API secret")

    from supabase_client import get_supabase
    db = get_supabase()
    db.rpc("add_coins", {
        "p_user_id": req.user_id,
        "p_amount": req.amount,
    }).execute()
    return {"success": True}


# ============================================================
# USER PROFILE ENDPOINT (for tier check from frontend)
# ============================================================
@app.get("/api/profile/{user_id}")
async def get_user_profile(user_id: str, x_api_secret: Optional[str] = Header(None)):
    """Get a user's profile including their tier."""
    if not verify_api_secret(x_api_secret):
        raise HTTPException(status_code=401, detail="Invalid API secret")

    from supabase_client import get_supabase
    db = get_supabase()
    result = (
        db.table("profiles")
        .select("id, email, full_name, tier, created_at")
        .eq("id", user_id)
        .limit(1)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Profile not found")
    return {"profile": result.data[0]}


# ============================================================
# START THE TASK WORKER IN THE BACKGROUND
# ============================================================
@app.on_event("startup")
async def startup_event():
    """Start the background task worker when server starts."""
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
