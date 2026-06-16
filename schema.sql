-- ============================================================
-- BAD DECISION AI — COMPLETE FRESH SCHEMA (v5)
-- ============================================================
-- Run this ENTIRE script in the Supabase SQL Editor.
-- It will DROP existing tables (if any) and create a fresh setup.
--
-- KEY DESIGN:
--   1. Clerk user IDs are TEXT (e.g. "user_3Ew45fAIqwEJ3naNttXUPMxTfFt") — NOT UUID.
--   2. No device_fingerprint column — multiple accounts per device allowed.
--   3. New users auto-get 50 free coins via the add_coins RPC after Clerk webhook.
--   4. Tier enforcement is in backend code, but the tier column tracks the user's plan.
-- ============================================================

-- ============================================================
-- DROP EXISTING TABLES (clean slate)
-- ============================================================
DROP TABLE IF EXISTS workspace_leads CASCADE;
DROP TABLE IF EXISTS smart_collections CASCADE;
DROP TABLE IF EXISTS global_intelligence_cache CASCADE;
DROP TABLE IF EXISTS tasks CASCADE;
DROP TABLE IF EXISTS usage_ledger CASCADE;
DROP TABLE IF EXISTS profiles CASCADE;

-- Drop existing functions
DROP FUNCTION IF EXISTS add_coins(text, integer);
DROP FUNCTION IF EXISTS deduct_coins(text, integer);
DROP FUNCTION IF EXISTS check_global_cache(text, text);
DROP FUNCTION IF EXISTS handle_new_user();

-- ============================================================
-- 1. PROFILES TABLE
-- ============================================================
-- Stores each user's basic info. id = Clerk user ID (TEXT).
CREATE TABLE profiles (
  id            TEXT PRIMARY KEY,
  email         TEXT UNIQUE NOT NULL,
  full_name     TEXT,
  tier          TEXT NOT NULL DEFAULT 'free' CHECK (tier IN ('free','starter','growth','pro')),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================
-- 2. USAGE LEDGER TABLE
-- ============================================================
-- Stores each user's coin balance. user_id = Clerk user ID (TEXT).
CREATE TABLE usage_ledger (
  user_id          TEXT PRIMARY KEY REFERENCES profiles(id) ON DELETE CASCADE,
  coins_balance    INTEGER NOT NULL DEFAULT 0 CHECK (coins_balance >= 0),
  coins_reserved   INTEGER NOT NULL DEFAULT 0 CHECK (coins_reserved >= 0),
  coins_lifetime   INTEGER NOT NULL DEFAULT 0 CHECK (coins_lifetime >= 0),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================
-- 3. TASKS TABLE
-- ============================================================
-- Each row = one search task. user_id = Clerk user ID (TEXT).
CREATE TABLE tasks (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id          TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  task_type        TEXT NOT NULL CHECK (task_type IN ('ads_intent','smb_maps','web_absent','social_intent')),
  query            TEXT NOT NULL,
  status           TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','processing','completed','exhausted','failed')),
  coins_reserved   INTEGER NOT NULL DEFAULT 0,
  country          TEXT,
  state_region     TEXT,
  error            TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_tasks_user_id ON tasks (user_id);
CREATE INDEX idx_tasks_status  ON tasks (status);
CREATE INDEX idx_tasks_created ON tasks (created_at DESC);

-- ============================================================
-- 4. GLOBAL INTELLIGENCE CACHE TABLE
-- ============================================================
-- Cache of verified companies and their contact info.
CREATE TABLE global_intelligence_cache (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  domain_hash       TEXT UNIQUE,
  company_name      TEXT,
  website_url       TEXT,
  dm_name           TEXT,
  dm_position       TEXT,
  verified_email    TEXT,
  is_catchall       BOOLEAN DEFAULT FALSE,
  linkedin          TEXT,
  instagram         TEXT,
  phone             TEXT,
  address           TEXT,
  ad_platform       TEXT,
  aggregator_source TEXT,
  aggregator_url    TEXT,
  platform          TEXT,
  intent_text       TEXT,
  verified_at       TIMESTAMPTZ,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_cache_domain_hash ON global_intelligence_cache (domain_hash);
CREATE INDEX idx_cache_company     ON global_intelligence_cache (company_name);
CREATE INDEX idx_cache_website     ON global_intelligence_cache (website_url);

-- ============================================================
-- 5. SMART COLLECTIONS TABLE
-- ============================================================
-- Each search task produces one smart collection.
CREATE TABLE smart_collections (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  task_id     UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  name        TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_collections_user_id ON smart_collections (user_id);
CREATE INDEX idx_collections_task_id ON smart_collections (task_id);

-- ============================================================
-- 6. WORKSPACE LEADS TABLE
-- ============================================================
-- Each lead belongs to a smart collection and links to the global cache.
CREATE TABLE workspace_leads (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  collection_id     UUID NOT NULL REFERENCES smart_collections(id) ON DELETE CASCADE,
  task_id           UUID REFERENCES tasks(id) ON DELETE CASCADE,
  user_id           TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  cache_id          UUID REFERENCES global_intelligence_cache(id),
  domain_hash       TEXT,
  company_name      TEXT,
  website_url       TEXT,
  dm_name           TEXT,
  dm_position       TEXT,
  verified_email    TEXT,
  is_catchall       BOOLEAN DEFAULT FALSE,
  linkedin          TEXT,
  instagram         TEXT,
  phone             TEXT,
  address           TEXT,
  ad_platform       TEXT,
  aggregator_source TEXT,
  aggregator_url    TEXT,
  platform          TEXT,
  intent_text       TEXT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_leads_collection_id ON workspace_leads (collection_id);
CREATE INDEX idx_leads_task_id       ON workspace_leads (task_id);
CREATE INDEX idx_leads_user_id       ON workspace_leads (user_id);

-- ============================================================
-- 7. RPC FUNCTIONS — add_coins and deduct_coins
-- ============================================================
-- These are called by the backend (and Paystack webhook) to safely
-- update a user's coin balance with row-level locking.

CREATE OR REPLACE FUNCTION add_coins(p_user_id TEXT, p_amount INTEGER)
RETURNS BOOLEAN AS $$
DECLARE
  row_count INTEGER;
BEGIN
  IF p_amount <= 0 THEN
    RETURN FALSE;
  END IF;

  UPDATE usage_ledger
  SET
    coins_balance  = coins_balance  + p_amount,
    coins_lifetime = coins_lifetime + p_amount,
    updated_at     = now()
  WHERE user_id = p_user_id;

  GET DIAGNOSTICS row_count = ROW_COUNT;

  -- If the ledger row doesn't exist yet (brand-new user), create it
  IF row_count = 0 THEN
    INSERT INTO usage_ledger (user_id, coins_balance, coins_reserved, coins_lifetime)
    VALUES (p_user_id, p_amount, 0, p_amount)
    ON CONFLICT (user_id) DO UPDATE
      SET coins_balance  = usage_ledger.coins_balance  + p_amount,
          coins_lifetime = usage_ledger.coins_lifetime + p_amount,
          updated_at     = now();
  END IF;

  RETURN TRUE;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION deduct_coins(p_user_id TEXT, p_amount INTEGER)
RETURNS BOOLEAN AS $$
DECLARE
  current_balance INTEGER;
BEGIN
  IF p_amount <= 0 THEN
    RETURN FALSE;
  END IF;

  SELECT coins_balance INTO current_balance
  FROM usage_ledger
  WHERE user_id = p_user_id
  FOR UPDATE;

  IF NOT FOUND THEN
    RETURN FALSE;
  END IF;

  IF current_balance < p_amount THEN
    RETURN FALSE;
  END IF;

  UPDATE usage_ledger
  SET
    coins_balance  = coins_balance  - p_amount,
    coins_reserved = coins_reserved + p_amount,
    updated_at     = now()
  WHERE user_id = p_user_id;

  RETURN TRUE;
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- 8. CHECK_GLOBAL_CACHE RPC
-- ============================================================
CREATE OR REPLACE FUNCTION check_global_cache(p_company_name TEXT, p_website_url TEXT)
RETURNS TABLE (
  id UUID,
  company_name TEXT,
  website_url TEXT,
  verified_email TEXT,
  dm_name TEXT,
  dm_position TEXT,
  phone TEXT,
  linkedin TEXT,
  verified_at TIMESTAMPTZ
) AS $$
BEGIN
  RETURN QUERY
  SELECT
    g.id, g.company_name, g.website_url, g.verified_email,
    g.dm_name, g.dm_position, g.phone, g.linkedin, g.verified_at
  FROM global_intelligence_cache g
  WHERE
    (p_company_name IS NOT NULL AND p_company_name <> '' AND g.company_name ILIKE p_company_name)
    OR
    (p_website_url IS NOT NULL AND p_website_url <> '' AND g.website_url = p_website_url)
  LIMIT 10;
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- 9. ROW LEVEL SECURITY (RLS)
-- ============================================================
-- The backend uses the SERVICE ROLE key which bypasses RLS.
-- These policies only apply when the ANON key is used (e.g. direct browser calls).
-- We disable RLS for now since the backend handles all auth via Clerk.

ALTER TABLE profiles                   DISABLE ROW LEVEL SECURITY;
ALTER TABLE usage_ledger               DISABLE ROW LEVEL SECURITY;
ALTER TABLE tasks                      DISABLE ROW LEVEL SECURITY;
ALTER TABLE global_intelligence_cache  DISABLE ROW LEVEL SECURITY;
ALTER TABLE smart_collections          DISABLE ROW LEVEL SECURITY;
ALTER TABLE workspace_leads            DISABLE ROW LEVEL SECURITY;

-- ============================================================
-- DONE. The database is ready.
-- ============================================================
-- The Clerk webhook (Next.js side) will create a profile row and a
-- usage_ledger row with 50 free coins when a new user signs up.
-- No manual setup needed beyond running this SQL.
