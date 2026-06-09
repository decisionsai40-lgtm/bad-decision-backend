"""
BAD DECISION AI — FastAPI Main Server v2.0
===========================================
Complete backend with Paystack integration, pricing tiers,
4 search engines, and coin economy.

IMPORTANT: Clerk user IDs are TEXT, not UUIDs.
All user_id columns are TEXT type.
"""

from fastapi import FastAPI, HTTPException, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import uvicorn
import hmac
import hashlib
import json
import httpx

from config import (
    PORT, DEBUG, COIN_FREE_TRIAL, PRICING_TIERS, COIN_PACKAGES,
    PAYSTACK_SECRET_KEY, PAYSTACK_PUBLIC_KEY, PAYSTACK_WEBHOOK_SECRET,
    BACKEND_API_SECRET,
)

app = FastAPI(
    title="Bad Decision AI — Backend Engine",
    description="B2B lead intelligence with 4 search engines",
    version="2.0.0",
)

# ============================================================
# CORS
# ============================================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# PYDANTIC MODELS
# ============================================================
class SearchRequest(BaseModel):
    user_id: str
    task_type: str  # ads_intent, smb_maps, web_absent, social_intent
    query: str
    continent: str = ""
    country: str = ""
    state_region: str = ""
    coins_reserved: int = 0


class CoinTopUpRequest(BaseModel):
    user_id: str
    package_id: str  # micro, standard, bulk, enterprise


class PaystackInitRequest(BaseModel):
    user_id: str
    plan_type: str = ""  # free, starter, growth, pro
    package_id: str = ""  # for coin top-ups
    email: str = ""


# ============================================================
# HELPER — Get or create profile
# ============================================================
def _ensure_profile(db, user_id: str, email: str = "") -> dict:
    """Ensure the user has a profile and coin_balances row."""
    result = db.table("profiles").select("id, tier, email").eq("id", user_id).execute()

    if result.data and len(result.data) > 0:
        return result.data[0]

    # Create profile
    profile_data = {
        "id": user_id,
        "tier": "free",
        "email": email,
        "full_name": "",
    }
    profile_result = db.table("profiles").insert(profile_data).execute()

    # Create coin balance with free trial
    try:
        db.rpc("get_or_create_coin_balance", {"p_user_id": user_id}).execute()
    except Exception as e:
        print(f"[BACKEND] Coin balance creation error: {e}")
        try:
            db.table("coin_balances").insert({
                "user_id": user_id,
                "balance": COIN_FREE_TRIAL,
                "coins_reserved": 0,
                "total_purchased": COIN_FREE_TRIAL,
                "total_spent": 0,
            }).execute()
        except Exception as e2:
            print(f"[BACKEND] Coin balance fallback error: {e2}")

    return profile_result.data[0] if profile_result.data else profile_data


# ============================================================
# HEALTH CHECK
# ============================================================
@app.get("/")
def root():
    return {"status": "alive", "service": "Bad Decision AI Backend", "version": "2.0.0"}


@app.get("/health")
def health_check():
    try:
        from supabase_client import get_supabase
        db = get_supabase()
        db.table("profiles").select("id").limit(1).execute()
        db_status = "connected"
    except Exception as e:
        db_status = f"error: {str(e)}"

    return {"status": "healthy", "database": db_status}


# ============================================================
# PROFILE ENDPOINTS
# ============================================================
@app.get("/api/profile/{user_id}")
async def get_profile(user_id: str):
    """Get a user's profile by their Clerk ID."""
    from supabase_client import get_supabase
    db = get_supabase()

    profile = _ensure_profile(db, user_id)

    # Get coin balance
    ledger_result = db.table("coin_balances").select("*").eq("user_id", user_id).execute()
    raw_ledger = ledger_result.data[0] if ledger_result.data else {
        "user_id": user_id,
        "balance": COIN_FREE_TRIAL,
        "coins_reserved": 0,
        "total_purchased": COIN_FREE_TRIAL,
        "total_spent": 0,
    }

    tier_name = profile.get("tier", "free")
    tier_info = PRICING_TIERS.get(tier_name, PRICING_TIERS["free"])

    ledger = {
        "user_id": raw_ledger.get("user_id", user_id),
        "coins_balance": raw_ledger.get("balance", COIN_FREE_TRIAL),
        "coins_reserved": raw_ledger.get("coins_reserved", 0),
        "coins_lifetime": raw_ledger.get("total_purchased", COIN_FREE_TRIAL),
        "total_spent": raw_ledger.get("total_spent", 0),
    }

    return {
        "profile": profile,
        "ledger": ledger,
        "tier_info": tier_info,
    }


# ============================================================
# PRICING ENDPOINTS
# ============================================================
@app.get("/api/pricing")
async def get_pricing():
    """Get all pricing tiers and coin packages."""
    return {
        "tiers": PRICING_TIERS,
        "coin_packages": COIN_PACKAGES,
        "paystack_public_key": PAYSTACK_PUBLIC_KEY,
    }


@app.get("/api/pricing/{user_id}")
async def get_user_pricing(user_id: str):
    """Get pricing info relevant to a specific user."""
    from supabase_client import get_supabase
    db = get_supabase()

    profile = _ensure_profile(db, user_id)
    current_tier = profile.get("tier", "free")

    return {
        "current_tier": current_tier,
        "tier_info": PRICING_TIERS.get(current_tier, PRICING_TIERS["free"]),
        "available_tiers": PRICING_TIERS,
        "coin_packages": COIN_PACKAGES,
        "paystack_public_key": PAYSTACK_PUBLIC_KEY,
    }


# ============================================================
# PAYSTACK ENDPOINTS
# ============================================================
@app.post("/api/paystack/initialize")
async def paystack_initialize(req: PaystackInitRequest):
    """Initialize a Paystack transaction for subscription or coin top-up."""
    from supabase_client import get_supabase
    db = get_supabase()

    if not PAYSTACK_SECRET_KEY:
        raise HTTPException(status_code=500, detail="Paystack not configured")

    _ensure_profile(db, req.user_id, req.email)

    # Determine the amount
    amount = 0
    description = ""

    if req.plan_type:
        tier = PRICING_TIERS.get(req.plan_type)
        if not tier:
            raise HTTPException(status_code=400, detail=f"Invalid plan type: {req.plan_type}")
        amount = tier["price"]
        description = f"Bad Decision AI — {tier['name']} Plan (Monthly)"

    elif req.package_id:
        package = COIN_PACKAGES.get(req.package_id)
        if not package:
            raise HTTPException(status_code=400, detail=f"Invalid package: {req.package_id}")
        amount = package["price"]
        description = f"Bad Decision AI — {package['coins']} Coins Top-Up"

    else:
        raise HTTPException(status_code=400, detail="Must specify plan_type or package_id")

    if amount == 0:
        raise HTTPException(status_code=400, detail="Free tier does not require payment")

    # Create or get Paystack customer
    customer_code = ""
    try:
        customer_result = db.table("paystack_customers").select("*").eq("user_id", req.user_id).execute()
        if customer_result.data:
            customer_code = customer_result.data[0].get("paystack_customer_code", "")
    except Exception:
        pass

    # Initialize transaction with Paystack
    callback_url = os.getenv("PAYSTACK_CALLBACK_URL", "https://baddecision.ai/payment/verify")

    async with httpx.AsyncClient(timeout=30) as client:
        payload = {
            "amount": amount * 100,  # Paystack expects amount in kobo (cents)
            "email": req.email,
            "callback_url": callback_url,
            "metadata": {
                "user_id": req.user_id,
                "plan_type": req.plan_type,
                "package_id": req.package_id,
                "description": description,
            },
        }

        if customer_code:
            payload["customer"] = customer_code

        response = await client.post(
            "https://api.paystack.co/transaction/initialize",
            headers={
                "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
        )

        if response.status_code == 200:
            data = response.json().get("data", {})
            return {
                "success": True,
                "authorization_url": data.get("authorization_url"),
                "reference": data.get("reference"),
                "access_code": data.get("access_code"),
            }
        else:
            raise HTTPException(status_code=500, detail=f"Paystack error: {response.text[:200]}")

    raise HTTPException(status_code=500, detail="Payment initialization failed")


@app.post("/api/paystack/webhook")
async def paystack_webhook(request: Request):
    """Handle Paystack webhook events (subscription payments, coin top-ups)."""
    body = await request.body()
    signature = request.headers.get("x-paystack-signature", "")

    # Verify webhook signature
    if PAYSTACK_WEBHOOK_SECRET:
        expected_sig = hmac.new(
            PAYSTACK_WEBHOOK_SECRET.encode(),
            body,
            hashlib.sha512,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected_sig):
            raise HTTPException(status_code=401, detail="Invalid signature")

    event = json.loads(body)
    event_type = event.get("event", "")

    from supabase_client import get_supabase
    db = get_supabase()

    if event_type == "charge.success":
        data = event.get("data", {})
        metadata = data.get("metadata", {})
        user_id = metadata.get("user_id", "")
        plan_type = metadata.get("plan_type", "")
        package_id = metadata.get("package_id", "")
        reference = data.get("reference", "")

        if not user_id:
            return {"status": "ignored"}

        # Handle plan subscription payment
        if plan_type and plan_type != "free":
            tier = PRICING_TIERS.get(plan_type)
            if tier:
                # Update user tier
                db.table("profiles").update({"tier": plan_type}).eq("id", user_id).execute()

                # Credit monthly coins
                try:
                    db.rpc("add_coins", {
                        "p_user_id": user_id,
                        "p_amount": tier["coins_per_month"],
                        "p_transaction_type": "plan_allocation",
                        "p_description": f"{tier['name']} plan monthly allocation",
                        "p_reference_id": reference,
                    }).execute()
                except Exception as e:
                    print(f"[PAYSTACK] Coin allocation error: {e}")

                print(f"[PAYSTACK] User {user_id} upgraded to {plan_type}")

        # Handle coin top-up
        elif package_id:
            package = COIN_PACKAGES.get(package_id)
            if package:
                try:
                    db.rpc("add_coins", {
                        "p_user_id": user_id,
                        "p_amount": package["coins"],
                        "p_transaction_type": "top_up",
                        "p_description": f"Coin top-up: {package['coins']} coins",
                        "p_reference_id": reference,
                    }).execute()
                except Exception as e:
                    print(f"[PAYSTACK] Coin top-up error: {e}")

                print(f"[PAYSTACK] User {user_id} topped up {package['coins']} coins")

        return {"status": "success"}

    return {"status": "ignored"}


@app.get("/api/paystack/verify/{reference}")
async def paystack_verify(reference: str):
    """Verify a Paystack transaction by reference."""
    if not PAYSTACK_SECRET_KEY:
        raise HTTPException(status_code=500, detail="Paystack not configured")

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"https://api.paystack.co/transaction/verify/{reference}",
            headers={"Authorization": f"Bearer {PAYSTACK_SECRET_KEY}"},
        )

        if response.status_code == 200:
            data = response.json()
            return {"success": True, "data": data.get("data")}
        else:
            raise HTTPException(status_code=400, detail="Verification failed")


# ============================================================
# TASK ENDPOINTS
# ============================================================
@app.post("/api/tasks/create")
async def create_task(req: SearchRequest):
    """Create a new search task."""
    from supabase_client import get_supabase
    db = get_supabase()

    profile = _ensure_profile(db, req.user_id)
    tier = profile.get("tier", "free")
    tier_info = PRICING_TIERS.get(tier, PRICING_TIERS["free"])

    # Check if engine is available for this tier
    if req.task_type not in tier_info.get("engines", []):
        raise HTTPException(
            status_code=403,
            detail=f"Engine '{req.task_type}' not available on {tier_info['name']} plan. Upgrade to access it."
        )

    # Check daily search limit
    if tier_info.get("max_searches_per_day", -1) > 0:
        from datetime import datetime, timedelta
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        today_tasks = db.table("tasks").select("id").eq("user_id", req.user_id).gte("created_at", today_start).execute()
        if len(today_tasks.data) >= tier_info["max_searches_per_day"]:
            raise HTTPException(
                status_code=429,
                detail=f"Daily search limit reached ({tier_info['max_searches_per_day']}/day). Upgrade your plan for more."
            )

    # Check coin balance
    ledger_result = db.table("coin_balances").select("balance").eq("user_id", req.user_id).execute()
    if ledger_result.data:
        balance = ledger_result.data[0].get("balance", 0)
        if balance <= 0:
            raise HTTPException(status_code=402, detail="Insufficient coins. Please top up your balance.")

    result = db.table("tasks").insert({
        "user_id": req.user_id,
        "task_type": req.task_type,
        "query": req.query,
        "location": f"{req.country}, {req.state_region}".strip(", "),
        "continent": req.continent,
        "country": req.country,
        "state_region": req.state_region,
        "status": "pending",
        "coins_reserved": req.coins_reserved,
    }).execute()

    return {"success": True, "task": result.data}


@app.get("/api/tasks/{user_id}")
async def get_user_tasks(user_id: str, limit: int = 50):
    """Get all tasks for a user."""
    from supabase_client import get_supabase
    db = get_supabase()
    result = (
        db.table("tasks")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return {"tasks": result.data}


@app.get("/api/tasks/{user_id}/{task_type}")
async def get_user_tasks_by_type(user_id: str, task_type: str, limit: int = 50):
    """Get tasks for a user filtered by engine type."""
    from supabase_client import get_supabase
    db = get_supabase()
    result = (
        db.table("tasks")
        .select("*")
        .eq("user_id", user_id)
        .eq("task_type", task_type)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return {"tasks": result.data}


# ============================================================
# LEADS & COLLECTIONS ENDPOINTS
# ============================================================
@app.get("/api/leads/{collection_id}")
async def get_collection_leads(collection_id: str):
    """Get all leads in a Smart Collection."""
    from supabase_client import get_supabase
    db = get_supabase()
    result = (
        db.table("workspace_leads")
        .select("*, global_intelligence_cache(*)")
        .eq("collection_id", collection_id)
        .execute()
    )
    return {"leads": result.data}


@app.get("/api/collections/{user_id}")
async def get_user_collections(user_id: str):
    """Get all Smart Collections for a user."""
    from supabase_client import get_supabase
    db = get_supabase()
    result = (
        db.table("smart_collections")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .execute()
    )
    return {"collections": result.data}


@app.put("/api/leads/{lead_id}/status")
async def update_lead_status(lead_id: str, status: str):
    """Update a lead's status (new, contacted, responded, closed, archived)."""
    from supabase_client import get_supabase
    db = get_supabase()
    result = db.table("workspace_leads").update({"status": status}).eq("id", lead_id).execute()
    return {"success": True}


# ============================================================
# CACHE ENDPOINTS
# ============================================================
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
    """Deduct coins from a user's balance."""
    from supabase_client import get_supabase
    db = get_supabase()
    try:
        db.rpc("deduct_coins", {
            "p_user_id": user_id,
            "p_amount": amount,
        }).execute()
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/coins/add")
async def add_coins(user_id: str, amount: int, transaction_type: str = "top_up", description: str = ""):
    """Add coins to a user's balance."""
    from supabase_client import get_supabase
    db = get_supabase()
    try:
        db.rpc("add_coins", {
            "p_user_id": user_id,
            "p_amount": amount,
            "p_transaction_type": transaction_type,
            "p_description": description,
        }).execute()
    except Exception as e:
        print(f"[BACKEND] add_coins RPC error: {e}")
        # Fallback
        ledger_result = db.table("coin_balances").select("user_id").eq("user_id", user_id).execute()
        if not ledger_result.data:
            db.table("coin_balances").insert({
                "user_id": user_id,
                "balance": amount,
                "coins_reserved": 0,
                "total_purchased": amount,
                "total_spent": 0,
            }).execute()
        else:
            db.table("coin_balances").update({
                "balance": amount,
                "total_purchased": amount,
            }).eq("user_id", user_id).execute()

    return {"success": True}


@app.get("/api/coins/{user_id}")
async def get_coin_balance(user_id: str):
    """Get a user's coin balance."""
    from supabase_client import get_supabase
    db = get_supabase()

    _ensure_profile(db, user_id)

    result = db.table("coin_balances").select("*").eq("user_id", user_id).execute()

    if result.data:
        raw = result.data[0]
        balance = {
            "user_id": raw.get("user_id", user_id),
            "coins_balance": raw.get("balance", COIN_FREE_TRIAL),
            "coins_reserved": raw.get("coins_reserved", 0),
            "coins_lifetime": raw.get("total_purchased", COIN_FREE_TRIAL),
            "total_spent": raw.get("total_spent", 0),
        }
        return {"balance": balance}

    return {"balance": {
        "user_id": user_id,
        "coins_balance": COIN_FREE_TRIAL,
        "coins_reserved": 0,
        "coins_lifetime": COIN_FREE_TRIAL,
        "total_spent": 0,
    }}


# ============================================================
# CLERK WEBHOOK — Auto-create profile on signup
# ============================================================
@app.post("/api/clerk/webhook")
async def clerk_webhook(request: Request):
    """Handle Clerk webhook events (user.created, user.updated)."""
    body = await request.body()
    event = json.loads(body)
    event_type = event.get("type", "")

    from supabase_client import get_supabase
    db = get_supabase()

    if event_type == "user.created":
        user_data = event.get("data", {})
        user_id = user_data.get("id", "")
        email = user_data.get("email_addresses", [{}])[0].get("email_address", "") if user_data.get("email_addresses") else ""
        full_name = f"{user_data.get('first_name', '')} {user_data.get('last_name', '')}".strip()
        avatar_url = user_data.get("image_url", "")

        if user_id:
            _ensure_profile(db, user_id, email)
            # Update with real name/avatar
            db.table("profiles").update({
                "email": email,
                "full_name": full_name,
                "avatar_url": avatar_url,
            }).eq("id", user_id).execute()
            print(f"[CLERK] New user created: {user_id} ({email})")

    elif event_type == "user.updated":
        user_data = event.get("data", {})
        user_id = user_data.get("id", "")
        email = user_data.get("email_addresses", [{}])[0].get("email_address", "") if user_data.get("email_addresses") else ""
        full_name = f"{user_data.get('first_name', '')} {user_data.get('last_name', '')}".strip()
        avatar_url = user_data.get("image_url", "")

        if user_id:
            try:
                db.table("profiles").update({
                    "email": email,
                    "full_name": full_name,
                    "avatar_url": avatar_url,
                }).eq("id", user_id).execute()
            except Exception as e:
                print(f"[CLERK] User update error: {e}")

    return {"status": "ok"}


# ============================================================
# START THE TASK WORKER
# ============================================================
@app.on_event("startup")
async def startup_event():
    """Start the background task worker when server starts."""
    import asyncio
    from task_worker.worker import run_task_worker
    asyncio.create_task(run_task_worker())


# ============================================================
# RUN
# ============================================================
if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=DEBUG)
