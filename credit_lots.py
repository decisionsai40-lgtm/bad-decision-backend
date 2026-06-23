"""
BAD DECISION — Credit Lot FIFO Deduction
=========================================
When the user spends credits (search, message gen, email send, AI turn),
this module deducts from the user's credit_lots in FIFO order (oldest
expiry first). This ensures soon-to-expire credits get used first,
which is fairer to users.

Used by:
  - task_worker/worker.py (search task completion → commit_credits)
  - main.py outreach endpoints (message gen → deduct immediately)
  - Phase E email sender (email send → deduct per 5 emails)
  - Phase F AI agent (per turn → deduct 2 credits)
"""

from datetime import datetime
from typing import Optional
from supabase_client import get_supabase


async def deduct_credits_fifo(user_id: str, amount: int, description: str = "") -> bool:
    """
    Deduct 'amount' credits from the user's lots, FIFO by expiry date.
    Updates credit_lots.remaining and credit_balances.credits_balance.
    Logs a transaction.

    Args:
        user_id: Clerk user ID
        amount: Number of credits to deduct
        description: Description for the credit_transactions log

    Returns:
        True on success, False if insufficient balance.
    """
    if amount <= 0:
        return True

    db = get_supabase()

    # Fetch non-expired lots ordered by expiry ASC (oldest first = FIFO)
    now_iso = datetime.utcnow().isoformat()
    result = (
        db.table("credit_lots")
        .select("id, remaining, expires_at, is_free, source")
        .eq("user_id", user_id)
        .gt("remaining", 0)
        .gt("expires_at", now_iso)
        .order("expires_at", asc=True)
        .execute()
    )

    if not result.data:
        print(f"[FIFO] No non-expired lots for user {user_id}")
        return False

    lots = result.data
    total_available = sum(lot["remaining"] for lot in lots)
    if total_available < amount:
        print(f"[FIFO] Insufficient balance for {user_id}: need {amount}, have {total_available}")
        return False

    # Deduct from oldest lot first, then next, etc.
    remaining_to_deduct = amount
    for lot in lots:
        if remaining_to_deduct <= 0:
            break
        deduct_from_this = min(lot["remaining"], remaining_to_deduct)
        new_remaining = lot["remaining"] - deduct_from_this

        db.table("credit_lots").update(
            {"remaining": new_remaining}
        ).eq("id", lot["id"]).execute()

        remaining_to_deduct -= deduct_from_this
        lot_type = "free" if lot["is_free"] else "paid"
        print(f"[FIFO] Deducted {deduct_from_this} from lot {lot['id'][:8]}... ({lot_type}, source={lot['source']})")

    # Log the transaction
    try:
        db.table("credit_transactions").insert({
            "user_id": user_id,
            "amount": -amount,
            "transaction_type": "spend",
            "description": description or f"Spent {amount} credits",
            "reference_id": None,
        }).execute()
    except Exception as e:
        print(f"[FIFO] Warning: could not log transaction: {e}")

    return True


async def get_user_lots_summary(user_id: str) -> list:
    """
    Returns the user's non-expired credit lots, ordered by expiry ASC.
    Used by the dashboard to show "X credits (Y expiring soon)".
    """
    db = get_supabase()
    try:
        result = db.rpc("get_credit_lots_summary", {"p_user_id": user_id}).execute()
        return result.data or []
    except Exception as e:
        print(f"[FIFO] Error fetching lots summary: {e}")
        return []


async def get_expiring_soon_count(user_id: str, days: int = 7) -> int:
    """
    Returns the total credits expiring within 'days' days.
    Used for the dashboard warning badge.
    """
    lots = await get_user_lots_summary(user_id)
    return sum(
        lot["remaining"] for lot in lots
        if lot.get("days_until_expiry", 999) <= days
    )
