"""
BAD DECISION — Supabase Database Connection
============================================
Connects to the Supabase database using the SERVICE ROLE key (full access).
This bypasses RLS (Row Level Security) so the backend can read/write any
user's data. RLS policies protect the anon key (used by the browser).

Lazy singleton: client created on first call.
Does NOT crash at import time if env vars are missing.
"""

from supabase import create_client, Client
from config import SUPABASE_URL, SUPABASE_KEY

_supabase_client: Client | None = None


def get_supabase() -> Client:
    """
    Returns the Supabase client (lazy singleton).

    The client is created on the first call and reused for all subsequent calls.
    If SUPABASE_URL or SUPABASE_KEY are not set, raises RuntimeError.

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
