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

COIN FAIRNESS: Users are only charged for leads that have
at least one real contact field (email, phone, DM name, or LinkedIn).
If all leads have ABSENT data, the search is FREE (exhausted).
"""

import asyncio
from datetime import datetime
from typing import Dict, Any

from supabase_client import get_supabase
from config import TASK_POLL_INTERVAL, TASK_BATCH_SIZE, COIN_COST_SCAN, COIN_COST_DEEP, COIN_COST_SMTP
from engines import ENGINE_MAP
from dedup.hash_dedup import save_to_cache


def _count_valuable_leads(leads: list) -> int:
    """
    Count leads that have at least one non-ABSENT contact field.
    These are leads worth paying for — they have real contact info.

    A lead is "valuable" if it has at least one of:
    - verified_email (not ABSENT)
    - phone (not ABSENT)
    - dm_name (not ABSENT)
    - linkedin (not ABSENT)
    """
    valuable = 0
    for lead in leads:
        has_contact = False
        for field in ["verified_email", "phone", "dm_name", "linkedin"]:
            val = lead.get(field, "ABSENT")
            if val and val != "ABSENT":
                has_contact = True
                break
        if has_contact:
            valuable += 1
    return valuable


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
    5. Deduct coins ONLY for leads with real contact data
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

        # Step 4: Count valuable leads (leads with real contact info)
        valuable_count = _count_valuable_leads(leads)

        if valuable_count == 0:
            # All leads have ABSENT data — user gets nothing useful
            # Still save the leads (company names are somewhat useful)
            # But mark as exhausted and don't charge
            print(f"[WORKER] Found {len(leads)} leads but NONE have contact info — marking exhausted (free)")

            collection = await _create_smart_collection(
                user_id=user_id,
                name=query,
                task_type=task_type,
            )

            for lead in leads:
                await save_to_cache(lead)
                if collection:
                    await _link_lead_to_collection(
                        task_id=task_id,
                        collection_id=collection,
                        lead_hash=lead.get("domain_hash"),
                    )

            await _update_task_status(task_id, "exhausted")
            print(f"[WORKER] Coins NOT deducted (no contact data in leads)")
            return

        # Step 5: Create a Smart Collection for this search
        collection = await _create_smart_collection(
            user_id=user_id,
            name=query,
            task_type=task_type,
        )

        # Step 6: Save each lead and link it
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

        # Step 7: Fair coin deduction — only charge for valuable leads
        coins_per_lead = get_coin_cost(task_type, user_tier)
        actual_cost = coins_per_lead * valuable_count

        # Never charge more than what was reserved
        actual_cost = min(actual_cost, coins_reserved)

        # Always charge at least 1 coin if there are valuable leads (minimum viable charge)
        if actual_cost <= 0 and valuable_count > 0:
            actual_cost = 1

        if actual_cost > 0:
            await _deduct_coins(user_id, actual_cost)
            print(f"[WORKER] Deducted {actual_cost} coins from user {user_id} ({valuable_count}/{len(leads)} leads with contact data)")
        else:
            print(f"[WORKER] No coins deducted (cost calculated as 0)")

        # Step 8: Mark as completed
        await _update_task_status(task_id, "completed")
        print(f"[WORKER] Task {task_id} completed — {len(leads)} leads found ({valuable_count} with contact data)")

    except Exception as e:
        print(f"[WORKER] Task {task_id} FAILED: {e}")
        try:
            await _update_task_status(task_id, "failed")
        except Exception as status_err:
            print(f"[WORKER] Also failed to update task status: {status_err}")


async def _update_task_status(task_id: str, status: str):
    """Update a task's status in the database."""
    try:
        db = get_supabase()
        db.table("tasks").update({
            "status": status,
            "updated_at": datetime.utcnow().isoformat(),
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
