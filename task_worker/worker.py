"""
BAD DECISION AI — Background Task Worker
==========================================
This is the HEART of the backend. It runs in a constant loop:

1. Check the database for new "pending" tasks
2. Pick up a task and mark it "processing"
3. Run the correct search engine (based on task_type)
4. Save the results to the database
5. Mark the task "completed"
6. Go back to step 1

This loop never stops. It's like a worker at a factory —
always looking for the next job on the conveyor belt.
"""

import asyncio
from datetime import datetime
from typing import Dict, Any

from supabase_client import get_supabase
from config import TASK_POLL_INTERVAL, TASK_BATCH_SIZE, COIN_COST_SCAN, COIN_COST_DEEP, COIN_COST_SMTP
from engines import ENGINE_MAP
from dedup.hash_dedup import save_to_cache


async def run_task_worker():
    """
    The main worker loop. Runs forever in the background.

    Think of it like a mailman who keeps checking the post office
    for new mail to deliver. They never stop checking.
    """

    print("=" * 60)
    print("  BAD DECISION AI — Task Worker Started")
    print(f"  Checking for new tasks every {TASK_POLL_INTERVAL} seconds")
    print("=" * 60)

    while True:
        try:
            # Step 1: Check for pending tasks
            tasks = await _fetch_pending_tasks()

            if tasks:
                print(f"[WORKER] Found {len(tasks)} pending task(s)")

                # Step 2: Process each task
                for task in tasks:
                    await _process_task(task)

            # Step 3: Wait before checking again
            await asyncio.sleep(TASK_POLL_INTERVAL)

        except Exception as e:
            print(f"[WORKER] Error in main loop: {e}")
            await asyncio.sleep(TASK_POLL_INTERVAL)


async def _fetch_pending_tasks():
    """
    Look in the database for tasks with status = "pending".
    These are searches that users have submitted but haven't been processed yet.
    """

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


async def _process_task(task: Dict[str, Any]):
    """
    Process a single task from start to finish.

    Steps:
    1. Mark the task as "processing" so no other worker grabs it
    2. Run the correct search engine
    3. Save each lead to the global cache
    4. Link leads to the user's smart collection
    5. Deduct coins from the user's ledger
    6. Mark the task as "completed"
    """

    task_id = task.get("id")
    user_id = task.get("user_id")
    task_type = task.get("task_type")
    query = task.get("query")
    coins_reserved = task.get("coins_reserved", 0)

    # Get the user's tier from the joined profile
    user_tier = "free"
    profile = task.get("profiles")
    if profile:
        user_tier = profile.get("tier", "free")

    print(f"[WORKER] Processing task {task_id}: {task_type} — '{query}' (tier: {user_tier})")

    # Step 1: Mark as "processing"
    await _update_task_status(task_id, "processing")

    try:
        # Step 2: Run the correct engine
        engine_func = ENGINE_MAP.get(task_type)

        if not engine_func:
            print(f"[WORKER] Unknown task_type: {task_type}")
            await _update_task_status(task_id, "failed")
            return

        leads = await engine_func(query=query, user_tier=user_tier)

        # Step 3: Handle the results
        if not leads:
            # No leads found — mark as "exhausted"
            print(f"[WORKER] No leads found for task {task_id} — marking exhausted")
            await _update_task_status(task_id, "exhausted")

            # Exhausted tasks do NOT deduct coins
            print(f"[WORKER] Coins NOT deducted (exhausted — no results)")
            return

        # Step 4: Create a Smart Collection for this search
        collection = await _create_smart_collection(
            user_id=user_id,
            name=query,
            task_type=task_type,
        )

        # Step 5: Save each lead and link it
        for lead in leads:
            # Save to global cache first
            await save_to_cache(lead)

            # Link to the user's collection and task
            if collection:
                await _link_lead_to_collection(
                    task_id=task_id,
                    collection_id=collection,
                    lead_hash=lead.get("domain_hash"),
                )

        # Step 6: Deduct coins (only for successful, non-exhausted tasks)
        if coins_reserved > 0:
            await _deduct_coins(user_id, coins_reserved)
            print(f"[WORKER] Deducted {coins_reserved} coins from user {user_id}")

        # Step 7: Mark as completed
        await _update_task_status(task_id, "completed")
        print(f"[WORKER] Task {task_id} completed — {len(leads)} leads found")

    except Exception as e:
        print(f"[WORKER] Task {task_id} FAILED: {e}")
        await _update_task_status(task_id, "failed")


async def _update_task_status(task_id: str, status: str):
    """Update a task's status in the database."""
    try:
        db = get_supabase()
        db.table("tasks").update({
            "status": status,
        }).eq("id", task_id).execute()
    except Exception as e:
        print(f"[WORKER] Error updating task {task_id}: {e}")


async def _create_smart_collection(user_id: str, name: str, task_type: str) -> str:
    """Create a Smart Collection (folder) for this search's results."""
    try:
        db = get_supabase()
        result = db.table("smart_collections").insert({
            "user_id": user_id,
            "name": name,
            "task_type": task_type,
        }).execute()

        if result.data:
            collection_id = result.data[0].get("id")
            print(f"[WORKER] Created collection: {name} ({collection_id})")
            return collection_id

    except Exception as e:
        print(f"[WORKER] Error creating collection: {e}")

    return None


async def _link_lead_to_collection(task_id: str, collection_id: str, lead_hash: str):
    """Link a lead from the global cache to a user's collection and task."""
    try:
        db = get_supabase()
        db.table("workspace_leads").insert({
            "task_id": task_id,
            "collection_id": collection_id,
            "lead_hash": lead_hash,
        }).execute()
    except Exception as e:
        print(f"[WORKER] Error linking lead to collection: {e}")


async def _deduct_coins(user_id: str, amount: int):
    """Deduct coins from the user's ledger using the database function."""
    try:
        db = get_supabase()
        db.rpc("deduct_coins", {
            "p_user_id": user_id,
            "p_amount": amount,
        }).execute()
    except Exception as e:
        print(f"[WORKER] Error deducting coins: {e}")


def get_coin_cost(task_type: str, user_tier: str) -> int:
    """
    Calculate how many coins a search will cost.

    The cost depends on which validation gates the user's tier unlocks:
    - Free: Gate 1 only → 1 coin per lead
    - Starter: Gate 1 & 2 → 2 coins per lead
    - Growth: Gate 1 & 2 (with AI) → 2 coins per lead
    - Pro: Gate 1, 2 & 3 → 3 coins per lead
    """

    if user_tier == "pro":
        return COIN_COST_SMTP    # 3 coins
    elif user_tier in ("starter", "growth"):
        return COIN_COST_DEEP    # 2 coins
    else:
        return COIN_COST_SCAN    # 1 coin
