"""
BAD DECISION AI — Supabase Database Connection
===============================================
This file connects to the Supabase database.
We use the "service role" key which has FULL access to everything
(bypasses Row Level Security) because the Python backend needs
to read and write to any table.

FIX: Lazy initialization — don't crash at import time if env vars
are missing. Create the client on first use instead.
"""

from supabase import create_client, Client
from config import SUPABASE_URL, SUPABASE_KEY

# Lazy singleton — created on first call to get_supabase()
_supabase_client: Client | None = None


def get_supabase() -> Client:
    """
    Returns the Supabase client (lazy singleton).
    Use this in any file that needs to talk to the database.

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
                "Please add them to your .env file or Render environment."
            )
        _supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)

    return _supabase_client
