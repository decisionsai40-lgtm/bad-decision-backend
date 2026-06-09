"""
BAD DECISION AI — O(1) Hash Deduplication
==========================================
Before we scrape a website, we check if we've already found
this lead before. If we have (and it's less than 30 days old),
we return the cached data instantly for 0 COINS.

How it works:
1. Take the website URL and hash it using SHA-256
2. Check the global_intelligence_cache table for this hash
3. If found AND verified within 30 days → return cached data (FREE!)
4. If not found or stale → proceed with scraping

This means users NEVER pay for the same data twice.
The SHA-256 hash guarantees O(1) lookup speed —
even with millions of rows, the check is instant.
"""

import hashlib
from typing import Tuple, Optional, Dict, Any

from supabase_client import get_supabase
from config import CACHE_FRESHNESS_DAYS


def compute_hash(url: str) -> str:
    """
    Create a SHA-256 hash from a URL.

    Think of it like a fingerprint for a website.
    Every unique URL gets a unique fingerprint.
    The same URL always produces the same fingerprint.

    Args:
        url: The website URL to hash

    Returns:
        A 64-character hex string (the hash/fingerprint)
    """
    if not url or url == "ABSENT":
        url = "unknown"

    # Normalize: lowercase, strip trailing slashes
    url = url.lower().strip().rstrip("/")

    return hashlib.sha256(url.encode("utf-8")).hexdigest()


async def check_duplicate(domain_hash: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """
    Check if we already have this lead in our global cache.

    Args:
        domain_hash: The SHA-256 hash to look up

    Returns:
        (is_duplicate, cached_data)
        - is_duplicate: True = we already have this lead
        - cached_data: The lead data if found, None if not found
    """

    try:
        db = get_supabase()

        result = (
            db.table("global_intelligence_cache")
            .select("*")
            .eq("domain_hash", domain_hash)
            .execute()
        )

        if result.data and len(result.data) > 0:
            cached = result.data[0]

            # Check if the data is still fresh (within 30 days)
            from datetime import datetime, timedelta
            last_verified = cached.get("last_verified_at")

            if last_verified:
                # Parse the timestamp
                if isinstance(last_verified, str):
                    last_verified = datetime.fromisoformat(last_verified.replace("Z", "+00:00"))

                # Is it still fresh?
                if datetime.now(last_verified.tzinfo) - last_verified < timedelta(days=CACHE_FRESHNESS_DAYS):
                    print(f"[DEDUP] Cache HIT for {domain_hash[:12]}... — returning cached data (0 coins)")
                    return True, cached
                else:
                    print(f"[DEDUP] Cache STALE for {domain_hash[:12]}... — re-scraping")
                    return False, None

            # No timestamp but we have data — return it anyway
            return True, cached

        # Not found in cache
        return False, None

    except Exception as e:
        print(f"[DEDUP] Cache check error: {e}")
        return False, None


async def save_to_cache(lead: Dict[str, Any]) -> bool:
    """
    Save a verified lead to the global cache.

    This is called AFTER a lead passes all validation gates.
    Future users searching for the same business will get
    this data instantly for 0 coins.

    Args:
        lead: The lead dictionary to save

    Returns:
        True = saved successfully, False = error
    """

    try:
        db = get_supabase()

        # Only save the fields that belong in the global cache
        cache_data = {
            "domain_hash": lead.get("domain_hash"),
            "company_name": lead.get("company_name", "ABSENT"),
            "website_url": lead.get("website_url", "ABSENT"),
            "dm_name": lead.get("dm_name", "ABSENT"),
            "dm_position": lead.get("dm_position", "ABSENT"),
            "verified_email": lead.get("verified_email", "ABSENT"),
            "is_catchall": lead.get("is_catchall", False),
            "linkedin": lead.get("linkedin", "ABSENT"),
            "instagram": lead.get("instagram", "ABSENT"),
            "phone": lead.get("phone", "ABSENT"),
            # Engine-specific fields (only present in certain engine types)
            "ad_platform": lead.get("ad_platform", "ABSENT"),
            "address": lead.get("address", "ABSENT"),
            "aggregator_source": lead.get("aggregator_source", "ABSENT"),
            "aggregator_url": lead.get("aggregator_url", "ABSENT"),
            "platform": lead.get("platform", "ABSENT"),
            "intent_text": lead.get("intent_text", "ABSENT"),
            "engine_type": lead.get("engine_type", "smb_maps"),
            "engine_data": lead.get("engine_data", {}),
            "city": lead.get("city"),
            "postcode": lead.get("postcode"),
            "latitude": lead.get("latitude"),
            "longitude": lead.get("longitude"),
            "category": lead.get("category"),
            "rating": lead.get("rating"),
            "review_count": lead.get("review_count"),
            "email_source": lead.get("email_source"),
            "discovery_source": lead.get("discovery_source"),
        }

        # Remove ABSENT fields that don't have a column in the DB
        # (Supabase will error if we try to insert a column that doesn't exist)
        cache_data = {k: v for k, v in cache_data.items() if v is not None}

        # Use upsert (insert or update if hash already exists)
        result = (
            db.table("global_intelligence_cache")
            .upsert(cache_data, on_conflict="domain_hash")
            .execute()
        )

        print(f"[DEDUP] Saved {lead.get('company_name')} to global cache")
        return True

    except Exception as e:
        print(f"[DEDUP] Save error: {e}")
        return False
