"""
BAD DECISION — Query-Level Cache
=================================
Before we run a search, we check if we've already searched for
the same query + engine combination recently. If we have (within
30 days), we return the cached leads instantly.

IMPORTANT: Credits are STILL charged on cached queries (per the
handoff brief section 1 "Credits Are ALWAYS Deducted"). The cache
only speeds up the response — it does not make searches free.

How it works:
  1. Normalize the query (lowercase, trim, collapse spaces).
  2. Combine with the task_type (engine).
  3. Hash with SHA-256 to get a unique query_hash.
  4. Check global_intelligence_cache for this hash.
  5. If found AND verified within CACHE_FRESHNESS_DAYS (30) → return cached leads.
  6. If not found or stale → return None (engine will run).
"""

import hashlib
import json
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

from supabase_client import get_supabase
from config import CACHE_FRESHNESS_DAYS


def compute_query_hash(query: str, task_type: str) -> str:
    """
    Create a SHA-256 hash from a normalized query + task_type.

    This gives every unique (query, engine) combination a unique hash.
    The same query + engine always produces the same hash.
    """
    # Normalize: lowercase, strip, collapse whitespace
    normalized = " ".join(query.lower().strip().split())
    combined = f"{normalized}|{task_type}"
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


def compute_domain_hash(url: str) -> str:
    """
    Create a SHA-256 hash from a URL or company name.
    Used for within-task dedup (checking if the same lead appears twice).
    """
    if not url or url == "ABSENT":
        url = "unknown"
    url = url.lower().strip().rstrip("/")
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


async def check_query_cache(query_hash: str) -> Optional[List[Dict[str, Any]]]:
    """
    Check if we have fresh cached results for this query_hash.

    Returns:
        List of leads if cache hit (and fresh), None if cache miss or stale.
    """
    try:
        db = get_supabase()

        result = (
            db.table("global_intelligence_cache")
            .select("leads_json, lead_count, verified_at")
            .eq("query_hash", query_hash)
            .limit(1)
            .execute()
        )

        if not result.data:
            return None

        cached = result.data[0]
        verified_at = cached.get("verified_at")

        # Check freshness
        if verified_at:
            if isinstance(verified_at, str):
                verified_at = datetime.fromisoformat(verified_at.replace("Z", "+00:00"))

            age = datetime.now(verified_at.tzinfo) - verified_at
            if age > timedelta(days=CACHE_FRESHNESS_DAYS):
                print(f"[CACHE] STALE for {query_hash[:12]}... (age: {age.days} days) — re-running search")
                return None

        leads_json = cached.get("leads_json", [])
        lead_count = cached.get("lead_count", len(leads_json))

        print(f"[CACHE] HIT for {query_hash[:12]}... — returning {lead_count} cached leads")
        return leads_json

    except Exception as e:
        print(f"[CACHE] Check error: {e}")
        return None


async def save_query_cache(
    query_hash: str,
    query_text: str,
    task_type: str,
    leads: List[Dict[str, Any]],
) -> bool:
    """
    Save search results to the query cache for future queries.

    This is called AFTER a search completes successfully.
    Future users searching for the same query will get these results instantly.
    """
    try:
        db = get_supabase()

        # Serialize leads to JSON. Remove any non-serializable values.
        clean_leads = []
        for lead in leads:
            clean_lead = {}
            for k, v in lead.items():
                try:
                    json.dumps(v)  # Test if serializable
                    clean_lead[k] = v
                except (TypeError, ValueError):
                    clean_lead[k] = str(v)
            clean_leads.append(clean_lead)

        cache_data = {
            "query_hash": query_hash,
            "query_text": query_text,
            "task_type": task_type,
            "leads_json": clean_leads,
            "lead_count": len(clean_leads),
            "verified_at": datetime.utcnow().isoformat(),
        }

        # Upsert (insert or update if query_hash already exists)
        (
            db.table("global_intelligence_cache")
            .upsert(cache_data, on_conflict="query_hash")
            .execute()
        )

        print(f"[CACHE] Saved {len(clean_leads)} leads for query '{query_text[:50]}...'")
        return True

    except Exception as e:
        print(f"[CACHE] Save error: {e}")
        return False
