-- ============================================================
-- BAD DECISION — SCHEMA MIGRATION (add engine-specific fields)
-- ============================================================
-- Run this in Supabase SQL Editor to add new columns for
-- engine-specific lead data (ratings, reviews, ad status, etc.)
-- ============================================================

-- Add new columns to workspace_leads for engine-specific data
ALTER TABLE workspace_leads ADD COLUMN IF NOT EXISTS rating DECIMAL(2,1);
ALTER TABLE workspace_leads ADD COLUMN IF NOT EXISTS review_count INTEGER;
ALTER TABLE workspace_leads ADD COLUMN IF NOT EXISTS category TEXT;
ALTER TABLE workspace_leads ADD COLUMN IF NOT EXISTS ad_status TEXT;
ALTER TABLE workspace_leads ADD COLUMN IF NOT EXISTS aggregator_rating DECIMAL(2,1);
ALTER TABLE workspace_leads ADD COLUMN IF NOT EXISTS intent_level TEXT;
ALTER TABLE workspace_leads ADD COLUMN IF NOT EXISTS post_url TEXT;
ALTER TABLE workspace_leads ADD COLUMN IF NOT EXISTS author_username TEXT;

-- Done. The workspace_leads table now supports all engine-specific fields.
