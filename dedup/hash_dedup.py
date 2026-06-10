"""
BAD DECISION AI — O(1) Hash Deduplication
==========================================
SHA-256 hash lookup for instant cache checking.
Users NEVER pay for the same data twice.
"""

import hashlib
from typing import Tuple, Optional, Dict, Any
from datetime import datetime, timedelta

from supabase_client import get_supabase
from config import CACHE_FRESHNESS_DAYS


def compute_hash(url: str) -> str:
    """Create a SHA-256 hash from a URL."""
    if not url or url == "ABSENT":
        url = "unknown"
    url = url.lower().strip().rstrip("/")
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


async def check_duplicate(domain_hash: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """Check if we already have this lead in our global cache."""
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
            last_verified = cached.get("last_verified_at")

            if last_verified:
                if isinstance(last_verified, str):
                    last_verified = datetime.fromisoformat(last_verified.replace("Z", "+00:00"))
                if datetime.now(last_verified.tzinfo) - last_verified < timedelta(days=CACHE_FRESHNESS_DAYS):
                    print(f"[DEDUP] Cache HIT for {domain_hash[:12]}...")
                    return True, cached
                else:
                    print(f"[DEDUP] Cache STALE for {domain_hash[:12]}...")
                    return False, None

            return True, cached

        return False, None

    except Exception as e:
        print(f"[DEDUP] Cache check error: {e}")
        return False, None


async def save_to_cache(lead: Dict[str, Any]) -> bool:
    """Save a verified lead to the global cache."""
    try:
        db = get_supabase()

        cache_data = {
            "domain_hash": lead.get("domain_hash"),
            "company_name": lead.get("company_name", ""),
            "website_url": lead.get("website_url", ""),
            "dm_name": lead.get("dm_name", ""),
            "dm_position": lead.get("dm_position", ""),
            "verified_email": lead.get("verified_email", ""),
            "is_catchall": lead.get("is_catchall", False),
            "linkedin": lead.get("linkedin", ""),
            "instagram": lead.get("instagram", ""),
            "phone": lead.get("phone", ""),
            "ad_platform": lead.get("ad_platform", ""),
            "address": lead.get("address", ""),
            "aggregator_source": lead.get("aggregator_source", ""),
            "aggregator_url": lead.get("aggregator_url", ""),
            "platform": lead.get("platform", ""),
            "intent_text": lead.get("intent_text", ""),
            "email_source": lead.get("email_source", ""),
            "digital_presence_score": lead.get("digital_presence_score", 0),
            "opportunity": lead.get("opportunity", ""),
            "missing_services": lead.get("missing_services", ""),
            "poster_name": lead.get("poster_name", ""),
            "poster_profile_url": lead.get("poster_profile_url", ""),
            "post_url": lead.get("post_url", ""),
            "community_or_group": lead.get("community_or_group", ""),
            "intent_type": lead.get("intent_type", ""),
            "time_sensitivity": lead.get("time_sensitivity", ""),
            "suggested_response": lead.get("suggested_response", ""),
            "outreach_method": lead.get("outreach_method", ""),
        }

        # Remove None values
        cache_data = {k: v for k, v in cache_data.items() if v is not None}

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
