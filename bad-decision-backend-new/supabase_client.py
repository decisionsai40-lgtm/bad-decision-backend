"""
BAD DECISION AI — Supabase Database Connection
===============================================
Connects to the Supabase database using the SERVICE ROLE key (full access).

Lazy singleton: client created on first call.
Does NOT crash at import time if env vars are missing.
"""

from supabase import create_client, Client
from config import SUPABASE_URL, SUPABASE_KEY

_supabase_client: Client | None = None


def get_supabase() -> Client:
    """
    Returns the Supabase client (lazy singleton).

    Example:
        from supabase_client import get_supabase
        db = get_supabase()
        result = db.table("tasks").select("*").eq("status", "pending").execute()
    """
    global _supabase_client

    if _supabase_client is None:
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_KEY must be set in environment variables. "
                "Add them to your .env file or Render environment."
            )
        _supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)

    return _supabase_client
