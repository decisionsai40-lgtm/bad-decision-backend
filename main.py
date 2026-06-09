"""
BAD DECISION AI — FastAPI Main Server
======================================
This is the entry point for the entire Python backend.
It starts the web server and sets up all the routes.
"""

import hashlib
import hmac
import json
import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from config import PORT, DEBUG, PRICING_TIERS, COIN_TOPUP_PACKS, PAYSTACK_SECRET_KEY, PAYSTACK_BASE_URL

# Create the FastAPI app
app = FastAPI(
    title="Bad Decision AI — Backend Engine",
    description="The scraping and validation engine that powers Bad Decision AI",
    version="3.0.0",
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
        "version": "3.0.0",
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
    continent: str = "",
    country: str = "",
    region: str = "",
):
    """Create a new search task with optional location parameters."""
    from supabase_client import get_supabase
    db = get_supabase()
    task_data = {
        "user_id": user_id,
        "task_type": task_type,
        "query": query,
        "status": "pending",
        "coins_reserved": coins_reserved,
    }
    # Add location fields if provided
    if continent:
        task_data["continent"] = continent
    if country:
        task_data["country"] = country
    if region:
        task_data["region"] = region

    result = db.table("tasks").insert(task_data).execute()
    return {"success": True, "task": result.data}


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


@app.get("/api/collections/{user_id}")
async def get_user_collections(user_id: str):
    """Get all smart collections for a user with lead counts."""
    from supabase_client import get_supabase
    db = get_supabase()

    # Get all collections
    result = (
        db.table("smart_collections")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .execute()
    )

    collections = result.data or []

    # Add lead count to each collection
    for collection in collections:
        collection_id = collection.get("id")
        if collection_id:
            lead_count = (
                db.table("workspace_leads")
                .select("id", count="exact")
                .eq("collection_id", collection_id)
                .execute()
            )
            collection["lead_count"] = lead_count.count if hasattr(lead_count, 'count') else len(lead_count.data or [])
        else:
            collection["lead_count"] = 0

    return {"collections": collections}


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
# PRICING & PAYMENT ENDPOINTS
# ============================================================
@app.get("/api/pricing")
async def get_pricing():
    """Return all pricing tiers and coin top-up packs."""
    return {
        "tiers": PRICING_TIERS,
        "topup_packs": COIN_TOPUP_PACKS,
    }


@app.post("/api/payments/initialize")
async def initialize_payment(user_id: str, plan: str, email: str):
    """Initialize a Paystack transaction for subscription or coin purchase."""
    if not PAYSTACK_SECRET_KEY:
        raise HTTPException(status_code=500, detail="Paystack not configured")

    # Determine amount and type
    is_topup = plan in COIN_TOPUP_PACKS
    is_subscription = plan in PRICING_TIERS

    if not is_topup and not is_subscription:
        raise HTTPException(status_code=400, detail=f"Invalid plan: {plan}")

    if is_subscription:
        amount = PRICING_TIERS[plan]["price_monthly"]
        coins = PRICING_TIERS[plan]["coins_per_month"]
        payment_type = "subscription"
    else:
        amount = COIN_TOPUP_PACKS[plan]["price"]
        coins = COIN_TOPUP_PACKS[plan]["coins"]
        payment_type = "topup"

    # Initialize Paystack transaction
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"{PAYSTACK_BASE_URL}/transaction/initialize",
                headers={
                    "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "email": email,
                    "amount": amount * 100,  # Paystack expects kobo
                    "metadata": {
                        "user_id": user_id,
                        "plan": plan,
                        "coins": coins,
                        "payment_type": payment_type,
                    },
                    "channels": ["card", "bank", "ussd", "qr", "mobile_money", "bank_transfer"],
                },
            )

            if response.status_code != 200:
                raise HTTPException(
                    status_code=502,
                    detail=f"Paystack error: {response.text[:200]}"
                )

            data = response.json()
            return {
                "success": True,
                "authorization_url": data.get("data", {}).get("authorization_url"),
                "reference": data.get("data", {}).get("reference"),
                "amount": amount,
                "coins": coins,
                "payment_type": payment_type,
            }

    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Paystack request timed out")


@app.get("/api/payments/verify/{reference}")
async def verify_payment(reference: str):
    """Verify a Paystack payment and credit coins."""
    if not PAYSTACK_SECRET_KEY:
        raise HTTPException(status_code=500, detail="Paystack not configured")

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                f"{PAYSTACK_BASE_URL}/transaction/verify/{reference}",
                headers={"Authorization": f"Bearer {PAYSTACK_SECRET_KEY}"},
            )

            if response.status_code != 200:
                raise HTTPException(status_code=502, detail="Paystack verification failed")

            data = response.json()
            payment_data = data.get("data", {})

            if payment_data.get("status") != "success":
                return {"success": False, "status": payment_data.get("status"), "message": "Payment not successful"}

            # Extract metadata
            metadata = payment_data.get("metadata", {})
            user_id = metadata.get("user_id")
            plan = metadata.get("plan")
            coins = metadata.get("coins", 0)
            payment_type = metadata.get("payment_type")

            if not user_id:
                raise HTTPException(status_code=400, detail="Missing user_id in payment metadata")

            # Credit coins
            from supabase_client import get_supabase
            db = get_supabase()

            db.rpc("add_coins", {
                "p_user_id": user_id,
                "p_amount": coins,
            }).execute()

            # Log the transaction
            try:
                db.table("coin_transactions").insert({
                    "user_id": user_id,
                    "amount": coins,
                    "transaction_type": "credit",
                    "reason": f"{payment_type}_{plan}",
                    "reference": reference,
                }).execute()
            except Exception:
                pass  # Don't fail if log table doesn't exist yet

            # Update subscription tier if it's a subscription payment
            if payment_type == "subscription" and plan in PRICING_TIERS:
                try:
                    db.table("profiles").update({
                        "subscription_tier": plan,
                        "subscription_status": "active",
                    }).eq("id", user_id).execute()
                except Exception:
                    pass

            return {
                "success": True,
                "coins_credited": coins,
                "plan": plan,
                "payment_type": payment_type,
            }

    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Paystack verification timed out")


@app.post("/api/webhooks/paystack")
async def paystack_webhook(request: Request):
    """Handle Paystack webhook events for subscriptions and coin purchases."""
    if not PAYSTACK_SECRET_KEY:
        return {"status": "ignored"}

    # Read the request body
    body = await request.body()
    signature = request.headers.get("x-paystack-signature", "")

    # Verify Paystack signature
    computed_sig = hmac.new(
        PAYSTACK_SECRET_KEY.encode("utf-8"),
        body,
        hashlib.sha512,
    ).hexdigest()

    if signature != computed_sig:
        raise HTTPException(status_code=401, detail="Invalid signature")

    event = json.loads(body)
    event_type = event.get("event", "")
    event_data = event.get("data", {})

    from supabase_client import get_supabase
    db = get_supabase()

    if event_type == "charge.success":
        # Successful charge — credit coins
        metadata = event_data.get("metadata", {})
        user_id = metadata.get("user_id")
        coins = metadata.get("coins", 0)
        plan = metadata.get("plan", "")
        payment_type = metadata.get("payment_type", "")
        reference = event_data.get("reference", "")

        if user_id and coins:
            try:
                db.rpc("add_coins", {
                    "p_user_id": user_id,
                    "p_amount": coins,
                }).execute()

                # Log transaction
                try:
                    db.table("coin_transactions").insert({
                        "user_id": user_id,
                        "amount": coins,
                        "transaction_type": "credit",
                        "reason": f"webhook_{payment_type}_{plan}",
                        "reference": reference,
                    }).execute()
                except Exception:
                    pass

                # Update subscription if it's a subscription
                if payment_type == "subscription" and plan in PRICING_TIERS:
                    try:
                        db.table("profiles").update({
                            "subscription_tier": plan,
                            "subscription_status": "active",
                        }).eq("id", user_id).execute()
                    except Exception:
                        pass

                print(f"[PAYSTACK_WEBHOOK] Credited {coins} coins to {user_id} ({payment_type}: {plan})")
            except Exception as e:
                print(f"[PAYSTACK_WEBHOOK] Error crediting coins: {e}")

    elif event_type == "subscription.create":
        # New subscription created
        customer_email = event_data.get("customer", {}).get("email", "")
        subscription_code = event_data.get("subscription_code", "")
        plan_code = event_data.get("plan", {}).get("plan_code", "")

        # Find user by email and update subscription
        try:
            result = db.table("profiles").select("id").eq("email", customer_email).execute()
            if result.data:
                user_id = result.data[0]["id"]
                db.table("profiles").update({
                    "subscription_id": subscription_code,
                    "subscription_status": "active",
                }).eq("id", user_id).execute()
                print(f"[PAYSTACK_WEBHOOK] Subscription created for {customer_email}")
        except Exception as e:
            print(f"[PAYSTACK_WEBHOOK] Error handling subscription.create: {e}")

    elif event_type == "subscription.disable":
        # Subscription cancelled/disabled
        subscription_code = event_data.get("subscription_code", "")

        try:
            result = db.table("profiles").select("id").eq("subscription_id", subscription_code).execute()
            if result.data:
                user_id = result.data[0]["id"]
                db.table("profiles").update({
                    "subscription_tier": "free",
                    "subscription_status": "cancelled",
                }).eq("id", user_id).execute()
                print(f"[PAYSTACK_WEBHOOK] Subscription disabled for {subscription_code}")
        except Exception as e:
            print(f"[PAYSTACK_WEBHOOK] Error handling subscription.disable: {e}")

    return {"status": "received"}


@app.get("/api/subscription/{user_id}")
async def get_subscription(user_id: str):
    """Get user's current subscription and coin balance."""
    from supabase_client import get_supabase
    db = get_supabase()

    try:
        result = (
            db.table("profiles")
            .select("id, subscription_tier, subscription_status, subscription_id")
            .eq("id", user_id)
            .execute()
        )

        if not result.data:
            raise HTTPException(status_code=404, detail="User not found")

        profile = result.data[0]
        tier = profile.get("subscription_tier", "free")
        tier_info = PRICING_TIERS.get(tier, PRICING_TIERS["free"])

        # Get coin balance
        coin_result = db.rpc("get_coin_balance", {"p_user_id": user_id}).execute()
        coin_balance = coin_result.data if coin_result.data else 0

        return {
            "user_id": user_id,
            "subscription_tier": tier,
            "subscription_status": profile.get("subscription_status", "active"),
            "subscription_id": profile.get("subscription_id"),
            "tier_info": tier_info,
            "coin_balance": coin_balance,
        }

    except HTTPException:
        raise
    except Exception as e:
        # Fallback if get_coin_balance RPC doesn't exist yet
        return {
            "user_id": user_id,
            "subscription_tier": "free",
            "subscription_status": "active",
            "subscription_id": None,
            "tier_info": PRICING_TIERS["free"],
            "coin_balance": 0,
            "error": str(e),
        }


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
