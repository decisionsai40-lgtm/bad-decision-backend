"""
BAD DECISION — Background Task Worker
======================================
"""

import asyncio
from datetime import datetime
from typing import Dict, Any, Optional

from supabase_client import get_supabase
from config import (
    TASK_POLL_INTERVAL, TASK_BATCH_SIZE, MAX_CONCURRENT_TASKS,
    LEAD_TARGET_FREE, LEAD_TARGET_STARTER, LEAD_TARGET_GROWTH, LEAD_TARGET_PAID,
    CREDIT_COST_SCAN, CREDIT_COST_DEEP, CREDIT_COST_SMTP,
)
from engines import ENGINE_MAP
from dedup.hash_dedup import save_query_cache, compute_query_hash, check_query_cache


# ============================================================
# MAIN WORKER LOOP
# ============================================================
async def run_task_worker():
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)

    print("=" * 60)
    print("  BAD DECISION — Task Worker Started")
    print(f"  Polling every {TASK_POLL_INTERVAL}s | Max {MAX_CONCURRENT_TASKS} concurrent tasks")
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
        await _process_task(task)


# ============================================================
# FETCH PENDING TASKS
# ============================================================
async def _fetch_pending_tasks():
    try:
        db = get_supabase()
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
        return []


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
            "growth": LEAD_TARGET_GROWTH,    # 75
            "pro": LEAD_TARGET_PAID,         # 100
        }
        tier_cap = tier_caps.get(user_tier, LEAD_TARGET_FREE)
        lead_target = min(max_leads_by_credits, tier_cap)

        # Don't allow less than 5 (even if credits are low)
        lead_target = max(lead_target, 5)

        print(f"[WORKER] Lead target: {lead_target} (credits: {credits_reserved}, cost/lead: {credits_per_lead}, max_by_credits: {max_leads_by_credits})")

        await _update_task(task_id, progress=10, current_step=f"Searching for up to {lead_target} leads...")

        query_hash = compute_query_hash(query, task_type)

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

            # Run the engine with lead_target
            leads = await engine_func(
                query=query,
                user_tier=user_tier,
                country=country,
                state_region=state_region,
                lead_target=lead_target,
                progress_callback=_make_progress_callback(task_id),
            )

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
    """Save leads to workspace_leads with all engine-specific fields."""
    db = get_supabase()
    saved = 0
    seen_hashes = set()

    for lead in leads:
        domain_hash = lead.get("domain_hash")
        if domain_hash and domain_hash in seen_hashes:
            continue
        if domain_hash:
            seen_hashes.add(domain_hash)

        # All possible fields (engine-specific ones are optional)
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
            # New engine-specific fields (may be NULL if not applicable)
            "rating": lead.get("rating"),
            "review_count": lead.get("review_count"),
            "category": lead.get("category"),
            "ad_status": lead.get("ad_status"),
            "aggregator_rating": lead.get("aggregator_rating"),
            "intent_level": lead.get("intent_level"),
            "post_url": lead.get("post_url"),
            "author_username": lead.get("author_username"),
            # Outreach messages
            "outreach_email": lead.get("outreach_email"),
            "outreach_social": lead.get("outreach_social"),
            "outreach_call": lead.get("outreach_call"),
        }

        # Remove None values
        row = {k: v for k, v in row.items() if v is not None}

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
