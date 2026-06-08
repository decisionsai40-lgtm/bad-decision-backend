"""
BAD DECISION AI — Supabase Database Connection
===============================================
This file connects to the Supabase database.
We use the "service role" key which has FULL access to everything
(bypasses Row Level Security) because the Python backend needs
to read and write to any table.
"""

from supabase import create_client, Client
from config import SUPABASE_URL, SUPABASE_KEY

# Create the Supabase client (our connection to the database)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def get_supabase() -> Client:
    """
    Returns the Supabase client.
    Use this in any file that needs to talk to the database.

    Example:
        from supabase_client import get_supabase
        db = get_supabase()
        result = db.table("tasks").select("*").eq("status", "pending").execute()
    """
    return supabase
