"""
BAD DECISION AI — FastAPI Main Server
======================================
This is the entry point for the entire Python backend.
It starts the web server and sets up all the routes.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from config import PORT, DEBUG

# Create the FastAPI app
app = FastAPI(
    title="Bad Decision AI — Backend Engine",
    description="The scraping and validation engine that powers Bad Decision AI",
    version="1.0.0",
)

# ============================================================
# CORS — Allow the Next.js frontend to talk to this backend
# ============================================================
# CORS is like a bouncer at a club. By default, websites can't
# talk to other websites. This rule says "let our frontend through."
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, change this to your Vercel URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# HEALTH CHECK — Is the server alive?
# ============================================================
@app.get("/")
def root():
    """Simple check to see if the backend is running."""
    return {
        "status": "alive",
        "service": "Bad Decision AI Backend",
        "version": "1.0.0",
    }


@app.get("/health")
def health_check():
    """More detailed health check."""
    from supabase_client import get_supabase
    try:
        db = get_supabase()
        # Try a simple query to see if Supabase is reachable
        db.table("profiles").select("id").limit(1).execute()
        db_status = "connected"
    except Exception as e:
        db_status = f"error: {str(e)}"

    return {
        "status": "healthy",
        "database": db_status,
    }


# ============================================================
# TASK ENDPOINTS — For the frontend to submit and check tasks
# ============================================================
@app.post("/api/tasks/create")
async def create_task(
    user_id: str,
    task_type: str,
    query: str,
    coins_reserved: int = 0,
):
    """
    The Next.js frontend calls this when a user starts a search.
    It creates a new task in the database for the Python worker to pick up.
    """
    from supabase_client import get_supabase

    db = get_supabase()

    result = db.table("tasks").insert({
        "user_id": user_id,
        "task_type": task_type,
        "query": query,
        "status": "pending",
        "coins_reserved": coins_reserved,
    }).execute()

    return {"success": True, "task": result.data}


@app.get("/api/tasks/{user_id}")
async def get_user_tasks(user_id: str):
    """
    Get all tasks for a specific user.
    The frontend calls this to show task history.
    """
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
async def get_collection_leads(collection_id: str):
    """
    Get all leads in a specific Smart Collection.
    The frontend calls this when a user clicks a collection.
    """
    from supabase_client import get_supabase

    db = get_supabase()

    # Get workspace_leads joined with global_intelligence_cache data
    result = (
        db.table("workspace_leads")
        .select("*, global_intelligence_cache(*)")
        .eq("collection_id", collection_id)
        .execute()
    )

    return {"leads": result.data}


@app.get("/api/cache/check")
async def check_cache(company_name: str = "", website_url: str = ""):
    """
    Check the global cache for existing leads.
    If found, the frontend gets them instantly for 0 coins.
    """
    from supabase_client import get_supabase

    db = get_supabase()

    # Use the PostgreSQL function we created in the schema
    result = db.rpc("check_global_cache", {
        "p_company_name": company_name,
        "p_website_url": website_url,
    }).execute()

    return {"cache_hits": result.data}


@app.post("/api/coins/deduct")
async def deduct_coins(user_id: str, amount: int):
    """
    Deduct coins from a user's ledger.
    Only called by the backend after a task completes successfully.
    """
    from supabase_client import get_supabase

    db = get_supabase()
    result = db.rpc("deduct_coins", {
        "p_user_id": user_id,
        "p_amount": amount,
    }).execute()

    return {"success": result.data}


@app.post("/api/coins/add")
async def add_coins(user_id: str, amount: int):
    """
    Add coins to a user's ledger.
    Called when Paystack confirms a payment.
    """
    from supabase_client import get_supabase

    db = get_supabase()
    db.rpc("add_coins", {
        "p_user_id": user_id,
        "p_amount": amount,
    }).execute()

    return {"success": True}


# ============================================================
# START THE TASK WORKER IN THE BACKGROUND
# ============================================================
@app.on_event("startup")
async def startup_event():
    """
    When the server starts, begin the background task worker.
    This worker constantly polls the database for new pending tasks
    and processes them.
    """
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
