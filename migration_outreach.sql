-- ============================================================
-- BAD DECISION — OUTREACH + USER SETTINGS MIGRATION
-- ============================================================
-- Run this in Supabase SQL Editor to add outreach message columns
-- and user settings columns.
-- ============================================================

-- Add outreach message columns to workspace_leads
ALTER TABLE workspace_leads ADD COLUMN IF NOT EXISTS outreach_email TEXT;
ALTER TABLE workspace_leads ADD COLUMN IF NOT EXISTS outreach_social TEXT;
ALTER TABLE workspace_leads ADD COLUMN IF NOT EXISTS outreach_call TEXT;

-- Add user settings columns to profiles
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS user_service TEXT;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS target_audience TEXT;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS copywriting_style TEXT DEFAULT 'david_ogilvy';

-- Done. The database now supports outreach messages and user settings.
