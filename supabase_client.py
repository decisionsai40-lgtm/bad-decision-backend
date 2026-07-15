"""
BAD DECISION — Supabase Database Connection
============================================
Connects to the Supabase database using the SERVICE ROLE key (full access).
This bypasses RLS (Row Level Security) so the backend can read/write any
user's data. RLS policies protect the anon key (used by the browser).

Lazy singleton: client created on first call.
Does NOT crash at import time if env vars are missing.

Connection resilience:
    supabase-py v2 holds a single httpx client with a connection pool.
    After ~5 min of idle time, the PostgREST server closes the TCP
    connection, and the next query fails with
    `httpx.RemoteProtocolError: Server disconnected` (this was the
    "[WORKER] Error in stale task recovery: Server disconnected" log line).

    To recover, call `reset_supabase_client()` — the next `get_supabase()`
    call will create a fresh client with a fresh connection pool.
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


def reset_supabase_client() -> None:
    """
    Discard the cached Supabase client so the next `get_supabase()` call
    creates a fresh one with a new HTTP connection pool.

    Call this after a `httpx.RemoteProtocolError: Server disconnected` /
    `httpx.ConnectError` / `httpx.PoolTimeout` to recover without restarting
    the worker process.
    """
    global _supabase_client
    _supabase_client = None
