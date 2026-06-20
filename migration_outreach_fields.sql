-- ============================================================
-- BAD DECISION — SCHEMA MIGRATION (outreach messages + user settings)
-- ============================================================
-- Run this in Supabase SQL Editor to add:
-- 1. Outreach message columns to workspace_leads (email/social/call scripts)
-- 2. User settings columns to profiles (service, audience, copywriting style)
--
-- These are required for the on-demand outreach message generation feature.
-- ============================================================

-- ============================================================
-- 1. WORKSPACE LEADS — outreach message columns
-- ============================================================
ALTER TABLE workspace_leads ADD COLUMN IF NOT EXISTS outreach_email TEXT;
ALTER TABLE workspace_leads ADD COLUMN IF NOT EXISTS outreach_social TEXT;
ALTER TABLE workspace_leads ADD COLUMN IF NOT EXISTS outreach_call TEXT;

-- ============================================================
-- 2. PROFILES — user settings for outreach personalization
-- ============================================================
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS user_service TEXT DEFAULT '';
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS target_audience TEXT DEFAULT '';
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS copywriting_style TEXT DEFAULT 'david_ogilvy'
  CHECK (copywriting_style IN ('dan_kennedy','donald_miller','ray_edwards','david_ogilvy','jay_abraham','gary_halbert'));

-- Done. The backend can now generate and store personalized outreach messages.
