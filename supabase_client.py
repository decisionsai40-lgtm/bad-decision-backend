"""
BAD DECISION AI — Supabase Database Connection
===============================================
Uses the service role key for full access (bypasses RLS).
"""

from supabase import create_client, Client
from config import SUPABASE_URL, SUPABASE_KEY

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def get_supabase() -> Client:
    """Returns the Supabase client. Use in any file that needs DB access."""
    return supabase
