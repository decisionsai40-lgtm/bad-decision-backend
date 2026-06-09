"""
BAD DECISION AI — Background Task Worker v2.0
==============================================
Runs in a constant loop: fetch pending tasks, run the engine,
save results, deduct coins. Supports parallel engine execution.
"""

import asyncio
from datetime import datetime
from typing import Dict, Any

from supabase_client import get_supabase
from config import TASK_POLL_INTERVAL, TASK_BATCH_SIZE, COIN_COST_SCAN, COIN_COST_DEEP, COIN_COST_SMTP, PRICING_TIERS
from engines import ENGINE_MAP
from dedup.hash_dedup import save_to_cache


async def run_task_worker():
    """The main worker loop — runs forever in the background."""
    print("=" * 60)
    print("  BAD DECISION AI — Task Worker Started v2.0")
    print(f"  Checking every {TASK_POLL_INTERVAL}s")
    print("=" * 60)

    while True:
        try:
            tasks = await _fetch_pending_tasks()

            if tasks:
                print(f"[WORKER] Found {len(tasks)} pending task(s)")
                for task in tasks:
                    await _process_task(task)

            await asyncio.sleep(TASK_POLL_INTERVAL)

        except Exception as e:
            print(f"[WORKER] Main loop error: {e}")
            await asyncio.sleep(TASK_POLL_INTERVAL)


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
        print(f"[WORKER] Fetch error: {e}")
        return []


async def _process_task(task: Dict[str, Any]):
    """Process a single task from start to finish."""
    task_id = task.get("id")
    user_id = task.get("user_id")
    task_type = task.get("task_type")
    query = task.get("query")
    location = task.get("location", "")
    coins_reserved = task.get("coins_reserved", 0)

    user_tier = "free"
    profile = task.get("profiles")
    if profile:
        user_tier = profile.get("tier", "free")

    print(f"[WORKER] Processing {task_id}: {task_type} — '{query}' (tier: {user_tier})")

    await _update_task_status(task_id, "processing")

    try:
        engine_func = ENGINE_MAP.get(task_type)
        if not engine_func:
            print(f"[WORKER] Unknown task_type: {task_type}")
            await _update_task_status(task_id, "failed")
            return

        # Run the engine
        leads = await engine_func(query=query, user_tier=user_tier, location=location)

        if not leads:
            print(f"[WORKER] No leads for task {task_id} — exhausted")
            await _update_task_status(task_id, "exhausted")
            return

        # Create a Smart Collection
        collection = await _create_smart_collection(user_id, query, task_type)

        # Save each lead
        for lead in leads:
            await save_to_cache(lead)
            if collection:
                await _link_lead_to_collection(task_id, collection, lead.get("domain_hash"))

        # Calculate coin cost
        tier_info = PRICING_TIERS.get(user_tier, PRICING_TIERS["free"])
        coins_to_charge = _calculate_coin_cost(leads, user_tier)

        # Deduct coins
        if coins_to_charge > 0:
            await _deduct_coins(user_id, coins_to_charge)
            print(f"[WORKER] Charged {coins_to_charge} coins for {len(leads)} leads")

        # Update task with results
        db = get_supabase()
        db.table("tasks").update({
            "status": "completed",
            "coins_charged": coins_to_charge,
            "results_count": len(leads),
            "completed_at": datetime.utcnow().isoformat(),
        }).eq("id", task_id).execute()

        print(f"[WORKER] Task {task_id} completed — {len(leads)} leads")

    except Exception as e:
        print(f"[WORKER] Task {task_id} FAILED: {e}")
        db = get_supabase()
        db.table("tasks").update({
            "status": "failed",
            "error_message": str(e)[:500],
        }).eq("id", task_id).execute()


def _calculate_coin_cost(leads: list, user_tier: str) -> int:
    """Calculate how many coins to charge based on tier and lead count."""
    tier_info = PRICING_TIERS.get(user_tier, PRICING_TIERS["free"])

    if tier_info.get("smtp_verification"):
        return len(leads) * COIN_COST_SMTP
    elif tier_info.get("email_verification"):
        return len(leads) * COIN_COST_DEEP
    else:
        return len(leads) * COIN_COST_SCAN


async def _update_task_status(task_id: str, status: str):
    """Update a task's status in the database."""
    try:
        db = get_supabase()
        update_data = {"status": status}
        if status == "completed":
            update_data["completed_at"] = datetime.utcnow().isoformat()
        db.table("tasks").update(update_data).eq("id", task_id).execute()
    except Exception as e:
        print(f"[WORKER] Status update error: {e}")


async def _create_smart_collection(user_id: str, name: str, task_type: str) -> str:
    """Create a Smart Collection for this search's results."""
    try:
        db = get_supabase()
        result = db.table("smart_collections").insert({
            "user_id": user_id,
            "name": name,
            "task_type": task_type,
        }).execute()
        if result.data:
            collection_id = result.data[0].get("id")
            print(f"[WORKER] Created collection: {name}")
            return collection_id
    except Exception as e:
        print(f"[WORKER] Collection error: {e}")
    return None


async def _link_lead_to_collection(task_id: str, collection_id: str, lead_hash: str):
    """Link a lead from the global cache to a user's collection."""
    try:
        db = get_supabase()
        db.table("workspace_leads").insert({
            "task_id": task_id,
            "collection_id": collection_id,
            "lead_hash": lead_hash,
        }).execute()
    except Exception as e:
        print(f"[WORKER] Link error: {e}")


async def _deduct_coins(user_id: str, amount: int):
    """Deduct coins from the user's balance."""
    try:
        db = get_supabase()
        db.rpc("deduct_coins", {"p_user_id": user_id, "p_amount": amount}).execute()
    except Exception as e:
        print(f"[WORKER] Deduct coins error: {e}")
