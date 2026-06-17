"""
BAD DECISION — Background Task Worker
======================================
This is the HEART of the backend. It runs in a constant loop:

1. Check the database for new "pending" tasks
2. Pick up tasks and mark them "processing"
3. Run the correct search engine (based on task_type)
4. Save the results to the database
5. Commit credits on success / Refund credits on failure
6. Mark the task "completed" (or "exhausted" / "failed")

CREDIT FLOW (CRITICAL — was double-charging before):
  - Credits are RESERVED when the task is created (in main.py create_task).
  - On SUCCESS: the worker calls commit_credits(actual_spent) + refund_credits(remaining).
    The user pays only for leads actually found.
  - On FAILURE or EXHAUSTED (0 leads): the worker calls refund_credits(full_amount).
    The user pays nothing.

PROGRESS UPDATES:
  - The worker updates task.progress (0-100) and task.current_step
    throughout processing. The frontend polls this for the interactive UI.
"""

import asyncio
from datetime import datetime
from typing import Dict, Any, Optional

from supabase_client import get_supabase
from config import (
    TASK_POLL_INTERVAL, TASK_BATCH_SIZE, MAX_CONCURRENT_TASKS,
    LEAD_TARGET_FREE, LEAD_TARGET_PAID,
    CREDIT_COST_SCAN, CREDIT_COST_DEEP, CREDIT_COST_SMTP,
)
from engines import ENGINE_MAP
from dedup.hash_dedup import check_query_cache, save_query_cache, compute_query_hash


# ============================================================
# MAIN WORKER LOOP
# ============================================================
async def run_task_worker():
    """
    The main worker loop. Runs forever in the background.

    Processes up to MAX_CONCURRENT_TASKS at once using a semaphore.
    This is tuned for Render's free tier (512MB RAM).
    """

    print("=" * 60)
    print("  BAD DECISION — Task Worker Started")
    print(f"  Polling every {TASK_POLL_INTERVAL}s | Max {MAX_CONCURRENT_TASKS} concurrent tasks")
    print("=" * 60)

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)

    while True:
        try:
            tasks = await _fetch_pending_tasks()

            if tasks:
                print(f"[WORKER] Found {len(tasks)} pending task(s)")

                # Launch tasks concurrently (up to MAX_CONCURRENT_TASKS at once)
                coroutines = [
                    _process_task_with_semaphore(semaphore, task)
                    for task in tasks
                ]
                await asyncio.gather(*coroutines, return_exceptions=True)

            await asyncio.sleep(TASK_POLL_INTERVAL)

        except Exception as e:
            print(f"[WORKER] Error in main loop: {e}")
            await asyncio.sleep(TASK_POLL_INTERVAL)


async def _process_task_with_semaphore(semaphore: asyncio.Semaphore, task: Dict[str, Any]):
    """Wrapper that acquires the semaphore before processing a task."""
    async with semaphore:
        await _process_task(task)


# ============================================================
# FETCH PENDING TASKS
# ============================================================
async def _fetch_pending_tasks():
    """Look in the database for tasks with status = 'pending'."""
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
    """
    Process a single task from start to finish.

    Steps:
      1. Mark the task as "processing" with progress 5%
      2. Check the query cache (if fresh, return cached leads — but STILL charge credits)
      3. Run the correct search engine
      4. Save leads to workspace_leads
      5. Create a smart_collection
      6. COMMIT credits on success / REFUND on failure
      7. Mark the task "completed" / "exhausted" / "failed"
    """

    task_id = task.get("id")
    user_id = task.get("user_id")
    task_type = task.get("task_type")
    query = task.get("query", "")
    credits_reserved = task.get("credits_reserved", 0)
    country = task.get("country", "")
    state_region = task.get("state_region", "")

    # Get the user's tier from the joined profile
    user_tier = "free"
    profile = task.get("profiles")
    if profile:
        user_tier = profile.get("tier", "free")

    print(f"[WORKER] Processing task {task_id}: {task_type} — '{query}' (tier: {user_tier})")

    # Step 1: Mark as "processing" with progress 5%
    await _update_task(task_id, status="processing", progress=5, current_step="Starting search engine...")

    try:
        # Step 2: Check the query cache
        query_hash = compute_query_hash(query, task_type)

        await _update_task(task_id, progress=10, current_step="Checking cache for previous results...")

        cached_leads = await check_query_cache(query_hash)

        leads = []
        if cached_leads:
            # Cache HIT — use cached leads (but STILL charge credits per the brief)
            print(f"[WORKER] Cache HIT for query — returning {len(cached_leads)} cached leads (credits still charged)")
            leads = cached_leads
            await _update_task(task_id, progress=70, current_step=f"Found {len(leads)} cached leads. Saving...")
        else:
            # Cache MISS — run the engine
            await _update_task(task_id, progress=15, current_step="Fetching data from web sources...")

            engine_func = ENGINE_MAP.get(task_type)
            if not engine_func:
                print(f"[WORKER] Unknown task_type: {task_type}")
                await _fail_task(task_id, user_id, credits_reserved, f"Unknown engine: {task_type}")
                return

            # Run the engine with a progress callback
            leads = await engine_func(
                query=query,
                user_tier=user_tier,
                country=country,
                state_region=state_region,
                progress_callback=_make_progress_callback(task_id),
            )

            # Save to cache for future queries
            if leads:
                await save_query_cache(query_hash, query, task_type, leads)

        # Step 3: Handle the results
        if not leads:
            # No leads found — mark as "exhausted" and REFUND all credits
            print(f"[WORKER] No leads found for task {task_id} — marking exhausted, refunding {credits_reserved} credits")
            await _update_task(
                task_id,
                progress=100,
                current_step="No leads found. Refunding credits.",
                leads_found=0,
                credits_spent=0,
            )
            await _refund_credits(user_id, credits_reserved, f"Refund: task {task_id} returned no leads (exhausted)")
            await _update_task_status(task_id, "exhausted")
            return

        # Step 4: Save leads to workspace_leads
        await _update_task(task_id, progress=85, current_step=f"Saving {len(leads)} leads to your workspace...")

        saved_count = await _save_leads(task_id, user_id, leads)

        # Step 5: Create a smart_collection
        await _update_task(task_id, progress=90, current_step="Creating your lead collection...")
        collection_id = await _create_smart_collection(
            user_id=user_id,
            task_id=task_id,
            name=query,
            task_type=task_type,
            lead_count=saved_count,
        )

        # Step 6: COMMIT credits (pay for actual leads found)
        credits_per_lead = _get_credit_cost(user_tier)
        credits_spent = min(saved_count * credits_per_lead, credits_reserved)
        credits_to_refund = credits_reserved - credits_spent

        if credits_spent > 0:
            await _commit_credits(user_id, credits_spent, f"Search completed: {saved_count} leads x {credits_per_lead} credits")
            print(f"[WORKER] Committed {credits_spent} credits for {saved_count} leads")

        if credits_to_refund > 0:
            await _refund_credits(user_id, credits_to_refund, f"Partial refund: reserved {credits_reserved} but spent {credits_spent}")
            print(f"[WORKER] Refunded {credits_to_refund} unused credits")

        # Step 7: Mark as completed
        await _update_task(
            task_id,
            status="completed",
            progress=100,
            current_step=f"Search complete! Found {saved_count} leads.",
            leads_found=saved_count,
            credits_spent=credits_spent,
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
# HELPER: FAIL A TASK (refund all credits)
# ============================================================
async def _fail_task(task_id: str, user_id: str, credits_reserved: int, error_message: str):
    """Mark a task as failed and refund all reserved credits."""
    try:
        if credits_reserved > 0:
            await _refund_credits(user_id, credits_reserved, f"Refund: task {task_id} failed")
            print(f"[WORKER] Refunded {credits_reserved} credits for failed task {task_id}")
    except Exception as e:
        print(f"[WORKER] Error refunding credits for failed task {task_id}: {e}")

    await _update_task(
        task_id,
        status="failed",
        progress=100,
        current_step="Search failed. Credits refunded.",
        error_message=error_message,
        credits_spent=0,
        completed_at=datetime.utcnow().isoformat(),
    )


# ============================================================
# HELPER: SAVE LEADS TO workspace_leads
# ============================================================
async def _save_leads(task_id: str, user_id: str, leads: list) -> int:
    """
    Save leads to the workspace_leads table.
    Uses domain_hash for within-task dedup (skips duplicates).
    Returns the number of leads actually saved.
    """
    db = get_supabase()
    saved = 0
    seen_hashes = set()

    for lead in leads:
        domain_hash = lead.get("domain_hash")

        # Within-task dedup
        if domain_hash and domain_hash in seen_hashes:
            continue
        if domain_hash:
            seen_hashes.add(domain_hash)

        # Build the insert row with only the fields that exist in the schema
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
        }

        # Remove None values to avoid overwriting defaults
        row = {k: v for k, v in row.items() if v is not None}

        try:
            db.table("workspace_leads").insert(row).execute()
            saved += 1
        except Exception as e:
            print(f"[WORKER] Error saving lead {domain_hash}: {e}")

    return saved


# ============================================================
# HELPER: CREATE SMART COLLECTION
# ============================================================
async def _create_smart_collection(
    user_id: str,
    task_id: str,
    name: str,
    task_type: str,
    lead_count: int,
) -> Optional[str]:
    """Create a Smart Collection (folder) for this search's results."""
    try:
        db = get_supabase()
        result = db.table("smart_collections").insert({
            "user_id": user_id,
            "task_id": task_id,
            "name": name,
            "task_type": task_type,
            "lead_count": lead_count,
        }).execute()

        if result.data:
            collection_id = result.data[0].get("id")
            print(f"[WORKER] Created collection: {name} ({collection_id}) with {lead_count} leads")
            return collection_id

    except Exception as e:
        print(f"[WORKER] Error creating collection: {e}")

    return None


# ============================================================
# HELPER: UPDATE TASK STATUS / PROGRESS
# ============================================================
async def _update_task(task_id: str, **fields):
    """Update one or more fields on a task."""
    try:
        db = get_supabase()
        db.table("tasks").update(fields).eq("id", task_id).execute()
    except Exception as e:
        print(f"[WORKER] Error updating task {task_id}: {e}")


async def _update_task_status(task_id: str, status: str):
    """Update just the task status."""
    await _update_task(task_id, status=status, completed_at=datetime.utcnow().isoformat())


# ============================================================
# HELPER: CREDIT OPERATIONS (via Supabase RPCs)
# ============================================================
async def _commit_credits(user_id: str, amount: int, description: str):
    """Commit (spend) reserved credits on a successful search."""
    try:
        db = get_supabase()
        db.rpc("commit_credits", {
            "p_user_id": user_id,
            "p_amount": amount,
            "p_description": description,
        }).execute()
    except Exception as e:
        print(f"[WORKER] Error committing credits: {e}")


async def _refund_credits(user_id: str, amount: int, description: str):
    """Refund reserved credits back to the user's balance."""
    try:
        db = get_supabase()
        db.rpc("refund_credits", {
            "p_user_id": user_id,
            "p_amount": amount,
            "p_description": description,
        }).execute()
    except Exception as e:
        print(f"[WORKER] Error refunding credits: {e}")


# ============================================================
# HELPER: PROGRESS CALLBACK (passed to engines)
# ============================================================
def _make_progress_callback(task_id: str):
    """
    Create a progress callback that engines can call to update the task UI.
    The engine calls this with (progress_percent, step_message).
    """
    async def callback(progress: int, step: str):
        await _update_task(task_id, progress=progress, current_step=step)

    return callback


# ============================================================
# HELPER: CREDIT COST PER LEAD (by tier)
# ============================================================
def _get_credit_cost(user_tier: str) -> int:
    """
    How many credits each lead costs, based on the user's tier.
    - Free: 1 credit per lead (Gate 1 only)
    - Starter/Growth: 2 credits per lead (Gate 1 + 2)
    - Pro: 3 credits per lead (Gate 1 + 2 + 3)
    """
    if user_tier == "pro":
        return CREDIT_COST_SMTP    # 3 credits
    elif user_tier in ("starter", "growth"):
        return CREDIT_COST_DEEP    # 2 credits
    else:
        return CREDIT_COST_SCAN    # 1 credit
