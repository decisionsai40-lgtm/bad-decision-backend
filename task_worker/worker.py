"""
BAD DECISION — Background Task Worker
======================================
"""

import asyncio
from datetime import datetime
from typing import Dict, Any, Optional

from supabase_client import get_supabase, reset_supabase_client
from config import (
    TASK_POLL_INTERVAL, TASK_BATCH_SIZE, MAX_CONCURRENT_TASKS,
    LEAD_TARGET_FREE, LEAD_TARGET_STARTER, LEAD_TARGET_GROWTH, LEAD_TARGET_PAID,
    CREDIT_COST_SCAN, CREDIT_COST_DEEP, CREDIT_COST_SMTP,
    TASK_TIMEOUT,
)
from engines import ENGINE_MAP
from dedup.hash_dedup import save_query_cache, compute_query_hash, check_query_cache


# Tracks currently-running asyncio tasks by task_id, so stale recovery can
# CANCEL the zombie coroutine (not just mark the DB row as failed).
# Without this, a runaway DeepSeek/email-scrape loop keeps burning CPU even
# after the DB row says "failed".
_running_tasks: Dict[str, asyncio.Task] = {}


# ============================================================
# MAIN WORKER LOOP
# ============================================================
async def run_task_worker():
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)

    print("=" * 60)
    print("  BAD DECISION — Task Worker Started")
    print(f"  Polling every {TASK_POLL_INTERVAL}s | Max {MAX_CONCURRENT_TASKS} concurrent tasks")
    print(f"  Per-task hard timeout: {TASK_TIMEOUT}s")
    print("=" * 60)

    while True:
        try:
            tasks = await _fetch_pending_tasks()

            if tasks:
                print(f"[WORKER] Found {len(tasks)} pending task(s)")
                coroutines = [_process_task_with_semaphore(semaphore, task) for task in tasks]
                await asyncio.gather(*coroutines, return_exceptions=True)

            await asyncio.sleep(TASK_POLL_INTERVAL)

        except Exception as e:
            print(f"[WORKER] Error in main loop: {e}")
            await asyncio.sleep(TASK_POLL_INTERVAL)


async def _process_task_with_semaphore(semaphore: asyncio.Semaphore, task: Dict[str, Any]):
    async with semaphore:
        this_task = asyncio.current_task()
        task_id = task.get("id")
        if task_id and this_task:
            _running_tasks[task_id] = this_task
        try:
            await _process_task(task)
        finally:
            if task_id:
                _running_tasks.pop(task_id, None)


# ============================================================
# FETCH PENDING TASKS + RECOVER STALE TASKS
# ============================================================
async def _fetch_pending_tasks():
    try:
        db = get_supabase()

        # First, recover stale tasks — tasks stuck in "processing" for more
        # than 5 minutes. This happens when the worker crashes or Render
        # restarts the service mid-task. Without this, the task stays in
        # "processing" forever and the frontend polls endlessly.
        await _recover_stale_tasks(db)

        result = (
            db.table("tasks")
            .select("*, profiles(tier)")
            .eq("status", "pending")
            .order("created_at", desc=False)
            .limit(TASK_BATCH_SIZE)
            .execute()
        )
        return result.data or []
    except Exception as e:
        print(f"[WORKER] Error fetching tasks: {e}")
        # If supabase client is in a bad state (e.g. "Server disconnected"),
        # reset the singleton so the next poll gets a fresh connection.
        if "disconnect" in str(e).lower() or "closed" in str(e).lower() or "connection" in str(e).lower():
            print("[WORKER] Resetting Supabase client after connection error")
            reset_supabase_client()
        return []


async def _recover_stale_tasks(db):
    """Fail tasks stuck in 'processing' for more than 5 minutes.

    Two key fixes vs. the old version:
    1. On supabase-py 'Server disconnected' / RemoteProtocolError, RESET the
       singleton and retry once. The old code silently swallowed this error,
       so stale tasks were never recovered.
    2. CANCEL the zombie asyncio.Task if it's still running. The old code
       just marked the DB row as 'failed' while the actual coroutine kept
       running (burning DeepSeek quota, etc.).
    """
    try:
        from datetime import datetime, timedelta
        cutoff = (datetime.utcnow() - timedelta(minutes=5)).isoformat()

        # Find stale processing tasks
        try:
            stale_result = (
                db.table("tasks")
                .select("id, user_id, credits_reserved, query")
                .eq("status", "processing")
                .lt("updated_at", cutoff)
                .limit(10)
                .execute()
            )
        except Exception as conn_err:
            # Supabase-py's idle HTTP pool dies after ~5 min of inactivity
            # with RemoteProtocolError("Server disconnected"). Reset and retry.
            print(f"[WORKER] Stale recovery DB query failed ({conn_err}), resetting Supabase client and retrying once")
            reset_supabase_client()
            db = get_supabase()
            stale_result = (
                db.table("tasks")
                .select("id, user_id, credits_reserved, query")
                .eq("status", "processing")
                .lt("updated_at", cutoff)
                .limit(10)
                .execute()
            )

        if not stale_result.data:
            return

        for task in stale_result.data:
            task_id = task["id"]
            user_id = task.get("user_id", "")
            credits = task.get("credits_reserved", 0)
            query = task.get("query", "")

            print(f"[WORKER] Recovering stale task {task_id} (query='{query}') — was stuck in processing")

            # Cancel the zombie coroutine if it's still running in this worker
            zombie = _running_tasks.get(task_id)
            if zombie and not zombie.done():
                print(f"[WORKER] Cancelling zombie coroutine for task {task_id}")
                zombie.cancel()
                # Wait briefly for cancellation to propagate. Don't block the
                # worker loop — if the zombie is stuck in a non-cancellable
                # wait (rare), we let it die on its own.
                try:
                    await asyncio.wait_for(zombie, timeout=1.0)
                except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                    pass
            _running_tasks.pop(task_id, None)

            # Refund credits
            if credits > 0 and user_id:
                try:
                    db.rpc("refund_credits", {
                        "p_user_id": user_id,
                        "p_amount": credits,
                        "p_description": f"Refund: task {task_id} timed out (stale recovery)"
                    }).execute()
                except Exception as e:
                    print(f"[WORKER] Error refunding stale task: {e}")

            # Mark as failed
            db.table("tasks").update({
                "status": "failed",
                "progress": 100,
                "current_step": "Search timed out. The server restarted mid-search. Please try again.",
                "error_message": "Task timed out (stale recovery — worker restarted)",
                "credits_spent": 0,
                "completed_at": datetime.utcnow().isoformat(),
            }).eq("id", task_id).execute()

    except Exception as e:
        print(f"[WORKER] Error in stale task recovery: {e}")


# ============================================================
# PROCESS A SINGLE TASK
# ============================================================
async def _process_task(task: Dict[str, Any]):
    task_id = task.get("id")
    user_id = task.get("user_id")
    task_type = task.get("task_type")
    query = task.get("query", "")
    credits_reserved = task.get("credits_reserved", 0)
    country = task.get("country", "")
    state_region = task.get("state_region", "")

    user_tier = "free"
    profile = task.get("profiles")
    if profile:
        user_tier = profile.get("tier", "free")

    print(f"[WORKER] Processing task {task_id}: {task_type} — '{query}' (tier: {user_tier})")

    # Step 1: Mark as processing
    await _update_task(task_id, status="processing", progress=5, current_step="Starting search...")

    try:
        # Step 2: Calculate credit-aware lead target
        credits_per_lead = _get_credit_cost(user_tier)
        max_leads_by_credits = credits_reserved // credits_per_lead if credits_per_lead > 0 else 50

        # Cap at tier-specific limits
        tier_caps = {
            "free": LEAD_TARGET_FREE,       # 25
            "starter": LEAD_TARGET_STARTER,  # 50
            "growth": LEAD_TARGET_GROWTH,    # 200
            "pro": LEAD_TARGET_PAID,         # 400
        }
        tier_cap = tier_caps.get(user_tier, LEAD_TARGET_FREE)

        # Ecommerce engine gets much higher caps
        if task_type in ("ecommerce", "web_absent"):
            if user_tier == "growth":
                tier_cap = 500
            elif user_tier == "pro":
                tier_cap = 2000
        lead_target = min(max_leads_by_credits, tier_cap)

        # Don't allow less than 5 (even if credits are low)
        lead_target = max(lead_target, 5)

        print(f"[WORKER] Lead target: {lead_target} (credits: {credits_reserved}, cost/lead: {credits_per_lead}, max_by_credits: {max_leads_by_credits})")

        await _update_task(task_id, progress=10, current_step=f"Searching for up to {lead_target} leads...")

        query_hash = compute_query_hash(query, task_type, country, state_region)

        # === CACHE CHECK ===
        # Before hitting the live web, check if we have fresh cached results
        # for this exact (query, engine) combination. If we do (within 30 days),
        # use them — this is much faster and saves Serper/ScrapingAnt/DeepSeek quota.
        #
        # IMPORTANT: Credits are STILL charged on cache hits (per the handoff
        # brief section 1 "Credits Are ALWAYS Deducted"). The cache only
        # speeds up the response — it does not make searches free.
        cached_leads = await check_query_cache(query_hash)
        if cached_leads:
            print(f"[WORKER] Cache HIT for query='{query[:50]}' engine={task_type} — using {len(cached_leads)} cached leads")
            await _update_task(task_id, progress=50, current_step="Found cached results — loading instantly...")
            # Trim to lead_target (cache may have more than the user paid for)
            leads = cached_leads[:lead_target]
        else:
            await _update_task(task_id, progress=15, current_step="Fetching fresh data from web sources...")

            engine_func = ENGINE_MAP.get(task_type)
            if not engine_func:
                print(f"[WORKER] Unknown task_type: {task_type}")
                await _fail_task(task_id, user_id, credits_reserved, f"Unknown engine: {task_type}")
                return

            # Run the engine with a HARD DEADLINE.
            # Previously TASK_TIMEOUT was defined in config.py but NEVER used —
            # a single runaway DeepSeek call could hang the worker for 9+ minutes
            # (3 keys × 3 attempts × 60s per key). Now we cap the entire engine
            # call at TASK_TIMEOUT seconds. If it exceeds, we cancel and fail
            # the task with a clean refund.
            progress_cb = _make_progress_callback(task_id)
            try:
                leads = await asyncio.wait_for(
                    engine_func(
                        query=query,
                        user_tier=user_tier,
                        country=country,
                        state_region=state_region,
                        lead_target=lead_target,
                        progress_callback=progress_cb,
                    ),
                    timeout=TASK_TIMEOUT,
                )
            except asyncio.TimeoutError:
                print(f"[WORKER] Task {task_id} EXCEEDED {TASK_TIMEOUT}s hard deadline — cancelling and refunding")
                await _fail_task(
                    task_id, user_id, credits_reserved,
                    f"Search timed out after {TASK_TIMEOUT}s. This can happen with very broad queries — try narrowing your search."
                )
                return

        # Save to cache for database building
        if leads:
            await save_query_cache(query_hash, query, task_type, leads)

        # Step 3: Handle results
        if not leads:
            print(f"[WORKER] No leads found — marking exhausted, refunding {credits_reserved} credits")
            await _update_task(
                task_id, progress=100, current_step="No leads found. Refunding credits.",
                leads_found=0, credits_spent=0,
            )
            await _refund_credits(user_id, credits_reserved, f"Refund: task {task_id} returned no leads")
            await _update_task_status(task_id, "exhausted")
            return

        # Step 4: Save leads (outreach messages are generated on-demand, NOT automatically)
        await _update_task(task_id, progress=85, current_step=f"Saving {len(leads)} leads to your workspace...")
        saved_count = await _save_leads(task_id, user_id, leads)

        # Step 5: Create collection
        await _update_task(task_id, progress=90, current_step="Creating your lead collection...")
        await _create_smart_collection(user_id, task_id, query, task_type, saved_count)

        # Step 6: Commit credits (pay for actual leads found)
        credits_spent = min(saved_count * credits_per_lead, credits_reserved)
        credits_to_refund = credits_reserved - credits_spent

        if credits_spent > 0:
            await _commit_credits(user_id, credits_spent, f"Search completed: {saved_count} leads x {credits_per_lead} credits")
            print(f"[WORKER] Committed {credits_spent} credits for {saved_count} leads")

        if credits_to_refund > 0:
            await _refund_credits(user_id, credits_to_refund, f"Partial refund: reserved {credits_reserved} but spent {credits_spent}")
            print(f"[WORKER] Refunded {credits_to_refund} unused credits")

        # Step 7: Mark completed
        await _update_task(
            task_id, status="completed", progress=100,
            current_step=f"Search complete! Found {saved_count} leads.",
            leads_found=saved_count, credits_spent=credits_spent,
            completed_at=datetime.utcnow().isoformat(),
        )
        print(f"[WORKER] Task {task_id} COMPLETED — {saved_count} leads, {credits_spent} credits spent")

    except asyncio.CancelledError:
        # Propagate cancellation (from stale recovery or shutdown) without
        # marking as failed — stale recovery handles the DB state.
        print(f"[WORKER] Task {task_id} was CANCELLED")
        raise
    except asyncio.TimeoutError:
        print(f"[WORKER] Task {task_id} TIMED OUT")
        await _fail_task(task_id, user_id, credits_reserved, "Search timed out. Please try again.")
    except Exception as e:
        print(f"[WORKER] Task {task_id} FAILED: {e}")
        await _fail_task(task_id, user_id, credits_reserved, str(e))


# ============================================================
# HELPERS
# ============================================================
async def _fail_task(task_id: str, user_id: str, credits_reserved: int, error_message: str):
    try:
        if credits_reserved > 0:
            await _refund_credits(user_id, credits_reserved, f"Refund: task {task_id} failed")
    except Exception as e:
        print(f"[WORKER] Error refunding: {e}")

    await _update_task(
        task_id, status="failed", progress=100,
        current_step="Search failed. Credits refunded.",
        error_message=error_message, credits_spent=0,
        completed_at=datetime.utcnow().isoformat(),
    )


async def _save_leads(task_id: str, user_id: str, leads: list) -> int:
    """Save leads to workspace_leads with all engine-specific fields.

    BATCH INSERT — the old version inserted one row at a time, which meant
    50 leads = 50 round-trips to Postgres. Now we send them in one batch.
    """
    if not leads:
        return 0

    db = get_supabase()
    seen_hashes = set()
    rows = []

    for lead in leads:
        domain_hash = lead.get("domain_hash")
        if domain_hash and domain_hash in seen_hashes:
            continue
        if domain_hash:
            seen_hashes.add(domain_hash)

        row = {
            "task_id": task_id,
            "user_id": user_id,
            "domain_hash": domain_hash,
            "company_name": lead.get("company_name"),
            "website_url": lead.get("website_url"),
            "dm_name": lead.get("dm_name"),
            "dm_position": lead.get("dm_position"),
            "verified_email": lead.get("verified_email"),
            "is_catchall": lead.get("is_catchall", False),
            "linkedin": lead.get("linkedin"),
            "instagram": lead.get("instagram"),
            "facebook": lead.get("facebook"),
            "phone": lead.get("phone"),
            "ad_platform": lead.get("ad_platform"),
            "address": lead.get("address"),
            "aggregator_source": lead.get("aggregator_source"),
            "aggregator_url": lead.get("aggregator_url"),
            "platform": lead.get("platform"),
            "intent_text": lead.get("intent_text"),
            "validation_gates_passed": lead.get("validation_gates_passed", 0),
            "rating": lead.get("rating"),
            "review_count": lead.get("review_count"),
            "category": lead.get("category"),
            "ad_status": lead.get("ad_status"),
            "aggregator_rating": lead.get("aggregator_rating"),
            "intent_level": lead.get("intent_level"),
            "post_url": lead.get("post_url"),
            "author_username": lead.get("author_username"),
            "outreach_email": lead.get("outreach_email"),
            "outreach_social": lead.get("outreach_social"),
            "outreach_call": lead.get("outreach_call"),
            "ecommerce_platform": lead.get("ecommerce_platform"),
            "product_count": lead.get("product_count"),
            "product_categories": lead.get("product_categories"),
            "average_price": lead.get("average_price"),
            "price_range": lead.get("price_range"),
            "store_currency": lead.get("store_currency"),
            "estimated_revenue": lead.get("estimated_revenue"),
            "tech_stack": lead.get("tech_stack"),
            "uses_email_marketing": lead.get("uses_email_marketing"),
            "uses_ad_tracking": lead.get("uses_ad_tracking"),
            "uses_subscriptions": lead.get("uses_subscriptions"),
            "store_age_days": lead.get("store_age_days"),
            "social_media_links": lead.get("social_media_links"),
            "ad_platforms": lead.get("ad_platforms"),
            "ad_start_date": lead.get("ad_start_date"),
            "ad_creative_url": lead.get("ad_creative_url"),
            "estimated_monthly_ad_spend": lead.get("estimated_monthly_ad_spend"),
            "naics_code": lead.get("naics_code"),
            "naics_description": lead.get("naics_description"),
            "business_start_date": lead.get("business_start_date"),
            "company_officers": lead.get("company_officers"),
            "is_whatsapp": lead.get("is_whatsapp"),
            "is_telegram": lead.get("is_telegram"),
            "messaging_checked": lead.get("messaging_checked"),
        }

        # Remove None values
        row = {k: v for k, v in row.items() if v is not None}
        rows.append(row)

    if not rows:
        return 0

    # Try a single batch insert. If it fails (e.g. one row violates a constraint),
    # fall back to per-row inserts so one bad lead doesn't kill the whole batch.
    try:
        result = db.table("workspace_leads").insert(rows).execute()
        saved = len(result.data) if result.data else 0
        print(f"[WORKER] Batch-inserted {saved} leads in one call")
        return saved
    except Exception as batch_err:
        print(f"[WORKER] Batch insert failed ({batch_err}), falling back to per-row insert")
        saved = 0
        for row in rows:
            try:
                db.table("workspace_leads").insert(row).execute()
                saved += 1
            except Exception as e:
                print(f"[WORKER] Error saving lead: {e}")
        return saved


async def _create_smart_collection(user_id: str, task_id: str, name: str, task_type: str, lead_count: int):
    try:
        db = get_supabase()
        result = db.table("smart_collections").insert({
            "user_id": user_id, "task_id": task_id,
            "name": name, "task_type": task_type, "lead_count": lead_count,
        }).execute()
        if result.data:
            print(f"[WORKER] Created collection: {name} with {lead_count} leads")
    except Exception as e:
        print(f"[WORKER] Error creating collection: {e}")


async def _update_task(task_id: str, **fields):
    try:
        db = get_supabase()
        db.table("tasks").update(fields).eq("id", task_id).execute()
    except Exception as e:
        print(f"[WORKER] Error updating task {task_id}: {e}")
        # If the connection died, reset so the next call gets a fresh client
        if "disconnect" in str(e).lower() or "closed" in str(e).lower() or "connection" in str(e).lower():
            reset_supabase_client()


async def _update_task_status(task_id: str, status: str):
    await _update_task(task_id, status=status, completed_at=datetime.utcnow().isoformat())


async def _commit_credits(user_id: str, amount: int, description: str):
    """Commit reserved credits (deduct from credits_reserved) AND
    deduct from credit_lots via FIFO so the lot tracking stays accurate."""
    try:
        db = get_supabase()
        db.rpc("commit_credits", {"p_user_id": user_id, "p_amount": amount, "p_description": description}).execute()
        # Also deduct from lots (FIFO) so expiry tracking is correct
        from credit_lots import deduct_credits_fifo
        await deduct_credits_fifo(user_id, amount, f"Search completed: {description}")
    except Exception as e:
        print(f"[WORKER] Error committing credits: {e}")


async def _refund_credits(user_id: str, amount: int, description: str):
    try:
        db = get_supabase()
        db.rpc("refund_credits", {"p_user_id": user_id, "p_amount": amount, "p_description": description}).execute()
    except Exception as e:
        print(f"[WORKER] Error refunding credits: {e}")


def _make_progress_callback(task_id: str):
    async def callback(progress: int, step: str):
        await _update_task(task_id, progress=progress, current_step=step)
    return callback


def _get_credit_cost(user_tier: str) -> int:
    if user_tier == "pro":
        return CREDIT_COST_SMTP
    elif user_tier in ("starter", "growth"):
        return CREDIT_COST_DEEP
    else:
        return CREDIT_COST_SCAN
