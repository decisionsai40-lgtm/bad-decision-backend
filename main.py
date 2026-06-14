"""
BAD DECISION AI — FastAPI Main Server
======================================
This is the entry point for the entire Python backend.
It starts the web server and sets up all the routes.
"""

from fastapi import FastAPI, HTTPException
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
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
# TASK ENDPOINTS
# ============================================================
@app.post("/api/tasks/create")
async def create_task(
    user_id: str,
    task_type: str,
    query: str,
    coins_reserved: int = 0,
    country: str = "",
    state_region: str = "",
):
    """Create a new search task."""
    from supabase_client import get_supabase
    db = get_supabase()
    insert_data = {
        "user_id": user_id,
        "task_type": task_type,
        "query": query,
        "status": "pending",
        "coins_reserved": coins_reserved,
    }
    # Store country/region if the columns exist in the tasks table
    if country:
        insert_data["country"] = country
    if state_region:
        insert_data["state_region"] = state_region

    result = db.table("tasks").insert(insert_data).execute()
    return {"success": True, "task": result.data}


@app.get("/api/task/{task_id}")
async def get_task_status(task_id: str):
    """Get the status of a specific task by its ID."""
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
            # Try fetching leads via collection_id from smart_collections
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
async def get_user_tasks(user_id: str):
    """Get all tasks for a specific user."""
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
    """Get all leads in a specific Smart Collection."""
    from supabase_client import get_supabase
    db = get_supabase()
    result = (
        db.table("workspace_leads")
        .select("*, global_intelligence_cache(*)")
        .eq("collection_id", collection_id)
        .execute()
    )
    return {"leads": result.data}


@app.get("/api/cache/check")
async def check_cache(company_name: str = "", website_url: str = ""):
    """Check the global cache for existing leads."""
    from supabase_client import get_supabase
    db = get_supabase()
    result = db.rpc("check_global_cache", {
        "p_company_name": company_name,
        "p_website_url": website_url,
    }).execute()
    return {"cache_hits": result.data}


@app.post("/api/coins/deduct")
async def deduct_coins(user_id: str, amount: int):
    """Deduct coins from a user's ledger."""
    from supabase_client import get_supabase
    db = get_supabase()
    result = db.rpc("deduct_coins", {
        "p_user_id": user_id,
        "p_amount": amount,
    }).execute()
    return {"success": result.data}


@app.post("/api/coins/add")
async def add_coins(user_id: str, amount: int):
    """Add coins to a user's ledger after payment."""
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
