-- ============================================================
-- BAD DECISION — COMPLETE FRESH SCHEMA (v6)
-- ============================================================
-- Run this ENTIRE script in the Supabase SQL Editor.
-- It will DROP existing tables (if any) and create a fresh setup.
--
-- KEY DESIGN (per handoff brief):
--   1. Clerk user IDs are TEXT (e.g. "user_3Ew45fAIqwEJ3naNttXUPMxTfFt") — NOT UUID.
--   2. No device_fingerprint column — multiple accounts per device allowed.
--   3. "coins" renamed to "credits" everywhere (tables, columns, RPCs).
--   4. Credits are ALWAYS deducted on successful tasks (even cached queries).
--   5. Credit flow: RESERVE on task creation → COMMIT on success / REFUND on failure.
--   6. add_credits RPC is IDEMPOTENT (checks reference_id for duplicates).
--   7. RLS ENABLED on all tables. Anon key denied. Service role bypasses RLS.
--   8. Tasks table has progress + current_step for interactive UI (no WebSocket needed).
-- ============================================================

-- ============================================================
-- DROP EXISTING TABLES AND FUNCTIONS (clean slate)
-- ============================================================
DROP TABLE IF EXISTS workspace_leads CASCADE;
DROP TABLE IF EXISTS smart_collections CASCADE;
DROP TABLE IF EXISTS global_intelligence_cache CASCADE;
DROP TABLE IF EXISTS credit_transactions CASCADE;
DROP TABLE IF EXISTS tasks CASCADE;
DROP TABLE IF EXISTS credit_balances CASCADE;
DROP TABLE IF EXISTS profiles CASCADE;

-- Drop existing functions (old + new names for safety)
DROP FUNCTION IF EXISTS add_coins(text, integer);
DROP FUNCTION IF EXISTS deduct_coins(text, integer);
DROP FUNCTION IF EXISTS check_global_cache(text, text);
DROP FUNCTION IF EXISTS handle_new_user();
DROP FUNCTION IF EXISTS add_credits(text, integer, text, text, text);
DROP FUNCTION IF EXISTS reserve_credits(text, integer, text);
DROP FUNCTION IF EXISTS commit_credits(text, integer, text);
DROP FUNCTION IF EXISTS refund_credits(text, integer, text);
DROP FUNCTION IF EXISTS handle_new_user(text, text, text, text);

-- ============================================================
-- 1. PROFILES TABLE
-- ============================================================
-- Stores each user's basic info. id = Clerk user ID (TEXT).
CREATE TABLE profiles (
  id            TEXT PRIMARY KEY,
  email         TEXT UNIQUE NOT NULL,
  full_name     TEXT,
  tier          TEXT NOT NULL DEFAULT 'free' CHECK (tier IN ('free','starter','growth','pro')),
  country       TEXT DEFAULT 'US',
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================
-- 2. CREDIT BALANCES TABLE (renamed from usage_ledger)
-- ============================================================
-- Stores each user's credit balance. user_id = Clerk user ID (TEXT).
-- credits_balance   = spendable credits right now
-- credits_reserved  = locked for pending tasks (returned on failure)
-- total_purchased   = lifetime credits bought (never goes down)
CREATE TABLE credit_balances (
  user_id           TEXT PRIMARY KEY REFERENCES profiles(id) ON DELETE CASCADE,
  credits_balance   INTEGER NOT NULL DEFAULT 0 CHECK (credits_balance >= 0),
  credits_reserved  INTEGER NOT NULL DEFAULT 0 CHECK (credits_reserved >= 0),
  total_purchased   INTEGER NOT NULL DEFAULT 0 CHECK (total_purchased >= 0),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================
-- 3. CREDIT TRANSACTIONS TABLE (NEW — was missing entirely)
-- ============================================================
-- Every credit movement is logged here. This is the user's transaction history.
-- amount is POSITIVE for credits added, NEGATIVE for credits removed.
-- reference_id is used for idempotency (Paystack reference or task ID).
CREATE TABLE credit_transactions (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id          TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  amount           INTEGER NOT NULL,
  transaction_type TEXT NOT NULL CHECK (transaction_type IN (
                     'signup_bonus', 'purchase', 'search_debit',
                     'refund', 'tier_upgrade', 'reserve', 'commit'
                   )),
  description      TEXT,
  reference_id     TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Idempotency: a reference_id can only appear once per transaction_type.
-- This prevents double-crediting from Paystack webhook retries.
CREATE UNIQUE INDEX idx_credit_tx_ref_unique
  ON credit_transactions (reference_id, transaction_type)
  WHERE reference_id IS NOT NULL;

CREATE INDEX idx_credit_tx_user_id ON credit_transactions (user_id);
CREATE INDEX idx_credit_tx_created ON credit_transactions (created_at DESC);

-- ============================================================
-- 4. TASKS TABLE
-- ============================================================
-- Each row = one search task. user_id = Clerk user ID (TEXT).
-- progress (0-100) + current_step power the interactive UI (no WebSocket).
-- credits_reserved = amount locked at creation.
-- credits_spent    = final amount deducted (0 if failed/refunded).
CREATE TABLE tasks (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id          TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  task_type        TEXT NOT NULL CHECK (task_type IN ('ads_intent','smb_maps','web_absent','social_intent')),
  query            TEXT NOT NULL,
  status           TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
                     'pending','processing','completed','exhausted','failed'
                   )),
  credits_reserved INTEGER NOT NULL DEFAULT 0,
  credits_spent    INTEGER NOT NULL DEFAULT 0,
  leads_found      INTEGER NOT NULL DEFAULT 0,
  country          TEXT,
  state_region     TEXT,
  progress         INTEGER NOT NULL DEFAULT 0 CHECK (progress >= 0 AND progress <= 100),
  current_step     TEXT DEFAULT 'Queued for processing',
  error_message    TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at     TIMESTAMPTZ
);

CREATE INDEX idx_tasks_user_id ON tasks (user_id);
CREATE INDEX idx_tasks_status  ON tasks (status);
CREATE INDEX idx_tasks_created ON tasks (created_at DESC);

-- ============================================================
-- 5. GLOBAL INTELLIGENCE CACHE TABLE (query-level cache)
-- ============================================================
-- Cache of SEARCH RESULTS keyed by normalized query hash.
-- If the same query was searched within CACHE_FRESHNESS_DAYS (30),
-- we return cached leads instantly (but STILL charge credits).
CREATE TABLE global_intelligence_cache (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  query_hash    TEXT UNIQUE NOT NULL,
  query_text    TEXT NOT NULL,
  task_type     TEXT NOT NULL,
  leads_json    JSONB NOT NULL DEFAULT '[]'::jsonb,
  lead_count    INTEGER NOT NULL DEFAULT 0,
  verified_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_cache_query_hash ON global_intelligence_cache (query_hash);
CREATE INDEX idx_cache_task_type  ON global_intelligence_cache (task_type);

-- ============================================================
-- 6. SMART COLLECTIONS TABLE
-- ============================================================
-- Each search task produces one smart collection (a named folder of leads).
-- Users can also create custom collections later (task_id nullable for those).
CREATE TABLE smart_collections (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  task_id     UUID REFERENCES tasks(id) ON DELETE CASCADE,
  name        TEXT NOT NULL,
  task_type   TEXT,
  lead_count  INTEGER NOT NULL DEFAULT 0,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_collections_user_id ON smart_collections (user_id);
CREATE INDEX idx_collections_task_id ON smart_collections (task_id);

-- ============================================================
-- 7. WORKSPACE LEADS TABLE
-- ============================================================
-- Each lead belongs to a task (and thus to the task's collection).
-- domain_hash is used for within-task dedup (unique per task).
-- validation_gates_passed: 1 = DNS only, 2 = DNS + SMTP, 3 = DNS + SMTP + DeepSeek.
CREATE TABLE workspace_leads (
  id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  task_id                   UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  user_id                   TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  domain_hash               TEXT,
  company_name              TEXT,
  website_url               TEXT,
  dm_name                   TEXT,
  dm_position               TEXT,
  verified_email            TEXT,
  is_catchall               BOOLEAN DEFAULT FALSE,
  linkedin                  TEXT,
  instagram                 TEXT,
  facebook                  TEXT,
  phone                     TEXT,
  ad_platform               TEXT,
  address                   TEXT,
  aggregator_source         TEXT,
  aggregator_url            TEXT,
  platform                  TEXT,
  intent_text               TEXT,
  validation_gates_passed   INTEGER DEFAULT 0,
  created_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_leads_task_id ON workspace_leads (task_id);
CREATE INDEX idx_leads_user_id ON workspace_leads (user_id);
CREATE INDEX idx_leads_domain  ON workspace_leads (domain_hash);

-- ============================================================
-- 8. RPC: handle_new_user
-- ============================================================
-- Called by the Clerk webhook when a new user signs up.
-- Creates the profile row, the credit_balances row with 50 free credits,
-- and logs a signup_bonus transaction.
CREATE OR REPLACE FUNCTION handle_new_user(
  p_clerk_id  TEXT,
  p_email     TEXT,
  p_full_name TEXT,
  p_country   TEXT
) RETURNS BOOLEAN AS $$
BEGIN
  -- Insert profile (idempotent: if exists, do nothing)
  INSERT INTO profiles (id, email, full_name, tier, country)
  VALUES (p_clerk_id, p_email, p_full_name, 'free', COALESCE(NULLIF(p_country, ''), 'US'))
  ON CONFLICT (id) DO NOTHING;

  -- Insert credit_balances with 50 free credits (idempotent)
  INSERT INTO credit_balances (user_id, credits_balance, credits_reserved, total_purchased)
  VALUES (p_clerk_id, 50, 0, 50)
  ON CONFLICT (user_id) DO NOTHING;

  -- Log the signup bonus transaction (idempotent via reference_id)
  INSERT INTO credit_transactions (user_id, amount, transaction_type, description, reference_id)
  VALUES (p_clerk_id, 50, 'signup_bonus', '50 free credits for signing up', 'signup_' || p_clerk_id)
  ON CONFLICT (reference_id, transaction_type) DO NOTHING;

  RETURN TRUE;
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- 9. RPC: reserve_credits
-- ============================================================
-- Called when a task is CREATED. Moves credits from balance to reserved.
-- This LOCKS the credits so the user cannot spend them on another search
-- while this task is running.
CREATE OR REPLACE FUNCTION reserve_credits(
  p_user_id     TEXT,
  p_amount      INTEGER,
  p_description TEXT
) RETURNS BOOLEAN AS $$
DECLARE
  current_balance INTEGER;
BEGIN
  IF p_amount <= 0 THEN
    RETURN TRUE;
  END IF;

  SELECT credits_balance INTO current_balance
  FROM credit_balances
  WHERE user_id = p_user_id
  FOR UPDATE;

  IF NOT FOUND THEN
    RETURN FALSE;
  END IF;

  IF current_balance < p_amount THEN
    RETURN FALSE;
  END IF;

  UPDATE credit_balances
  SET credits_balance  = credits_balance  - p_amount,
      credits_reserved = credits_reserved + p_amount,
      updated_at       = now()
  WHERE user_id = p_user_id;

  INSERT INTO credit_transactions (user_id, amount, transaction_type, description, reference_id)
  VALUES (p_user_id, -p_amount, 'reserve', COALESCE(p_description, 'Credits reserved for search'), NULL);

  RETURN TRUE;
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- 10. RPC: commit_credits
-- ============================================================
-- Called when a task SUCCEEDS. Removes credits from reserved (they are gone).
-- p_amount is the ACTUAL amount spent (may be less than reserved if fewer
-- leads were found). The difference is automatically refunded by refund_credits.
CREATE OR REPLACE FUNCTION commit_credits(
  p_user_id     TEXT,
  p_amount      INTEGER,
  p_description TEXT
) RETURNS BOOLEAN AS $$
BEGIN
  IF p_amount <= 0 THEN
    RETURN TRUE;
  END IF;

  UPDATE credit_balances
  SET credits_reserved = GREATEST(credits_reserved - p_amount, 0),
      updated_at       = now()
  WHERE user_id = p_user_id;

  INSERT INTO credit_transactions (user_id, amount, transaction_type, description, reference_id)
  VALUES (p_user_id, -p_amount, 'commit', COALESCE(p_description, 'Credits spent on search'), NULL);

  RETURN TRUE;
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- 11. RPC: refund_credits
-- ============================================================
-- Called when a task FAILS or returns 0 leads.
-- Moves ALL reserved credits back to the balance.
-- Also called to refund the UNUSED portion of a partial success.
CREATE OR REPLACE FUNCTION refund_credits(
  p_user_id     TEXT,
  p_amount      INTEGER,
  p_description TEXT
) RETURNS BOOLEAN AS $$
BEGIN
  IF p_amount <= 0 THEN
    RETURN TRUE;
  END IF;

  UPDATE credit_balances
  SET credits_reserved = GREATEST(credits_reserved - p_amount, 0),
      credits_balance  = credits_balance + p_amount,
      updated_at       = now()
  WHERE user_id = p_user_id;

  INSERT INTO credit_transactions (user_id, amount, transaction_type, description, reference_id)
  VALUES (p_user_id, p_amount, 'refund', COALESCE(p_description, 'Credits refunded'), NULL);

  RETURN TRUE;
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- 12. RPC: add_credits (IDEMPOTENT)
-- ============================================================
-- Called by the Paystack webhook after a successful payment.
-- IDEMPOTENT: if a transaction with the same reference_id + 'purchase' type
-- already exists, this function returns TRUE without adding credits again.
-- This prevents double-crediting from Paystack webhook retries.
CREATE OR REPLACE FUNCTION add_credits(
  p_user_id          TEXT,
  p_amount           INTEGER,
  p_transaction_type TEXT,
  p_description      TEXT,
  p_reference_id     TEXT
) RETURNS BOOLEAN AS $$
DECLARE
  already_exists INTEGER;
  row_count      INTEGER;
BEGIN
  IF p_amount <= 0 THEN
    RETURN FALSE;
  END IF;

  -- IDEMPOTENCY CHECK: if reference_id is provided, check for duplicates
  IF p_reference_id IS NOT NULL AND p_reference_id <> '' THEN
    SELECT COUNT(*) INTO already_exists
    FROM credit_transactions
    WHERE reference_id = p_reference_id
      AND transaction_type = p_transaction_type;

    IF already_exists > 0 THEN
      -- This payment was already processed. Do NOT add credits again.
      RETURN TRUE;
    END IF;
  END IF;

  -- Add credits to balance
  UPDATE credit_balances
  SET credits_balance = credits_balance + p_amount,
      total_purchased = CASE
        WHEN p_transaction_type = 'purchase' THEN total_purchased + p_amount
        ELSE total_purchased
      END,
      updated_at = now()
  WHERE user_id = p_user_id;

  GET DIAGNOSTICS row_count = ROW_COUNT;

  -- If the balance row doesn't exist yet (brand-new user), create it
  IF row_count = 0 THEN
    INSERT INTO credit_balances (user_id, credits_balance, credits_reserved, total_purchased)
    VALUES (
      p_user_id, p_amount, 0,
      CASE WHEN p_transaction_type = 'purchase' THEN p_amount ELSE 0 END
    )
    ON CONFLICT (user_id) DO UPDATE
      SET credits_balance = credit_balances.credits_balance + p_amount,
          total_purchased = CASE
            WHEN p_transaction_type = 'purchase' THEN credit_balances.total_purchased + p_amount
            ELSE credit_balances.total_purchased
          END,
          updated_at = now();
  END IF;

  -- Log the transaction (idempotent via unique index on reference_id + transaction_type)
  INSERT INTO credit_transactions (user_id, amount, transaction_type, description, reference_id)
  VALUES (p_user_id, p_amount, p_transaction_type, p_description, p_reference_id)
  ON CONFLICT (reference_id, transaction_type) DO NOTHING;

  RETURN TRUE;
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- 13. ROW LEVEL SECURITY (RLS)
-- ============================================================
-- The backend uses the SERVICE ROLE key which BYPASSES RLS entirely.
-- The frontend's browser code uses the ANON key which is subject to RLS.
--
-- SECURITY MODEL:
--   - Browser (anon key): CANNOT access any table directly.
--     All browser requests MUST go through Next.js API routes (server-side,
--     using service_role) or the Python backend (service_role).
--   - Next.js API routes (service_role): full access, bypasses RLS.
--   - Python backend (service_role): full access, bypasses RLS.
--
-- This closes the security hole where anyone with the anon key could
-- read/write any user's data. Now the anon key is useless for data access.
--
-- The global_intelligence_cache is the ONLY table where anon can SELECT
-- (it contains no user-specific data, just cached search results).

-- Enable RLS on all tables
ALTER TABLE profiles                   ENABLE ROW LEVEL SECURITY;
ALTER TABLE credit_balances            ENABLE ROW LEVEL SECURITY;
ALTER TABLE credit_transactions        ENABLE ROW LEVEL SECURITY;
ALTER TABLE tasks                      ENABLE ROW LEVEL SECURITY;
ALTER TABLE global_intelligence_cache  ENABLE ROW LEVEL SECURITY;
ALTER TABLE smart_collections          ENABLE ROW LEVEL SECURITY;
ALTER TABLE workspace_leads            ENABLE ROW LEVEL SECURITY;

-- profiles: anon cannot access. service_role bypasses.
CREATE POLICY "profiles_deny_anon" ON profiles
  FOR ALL TO anon
  USING (false) WITH CHECK (false);

-- credit_balances: anon cannot access.
CREATE POLICY "credit_balances_deny_anon" ON credit_balances
  FOR ALL TO anon
  USING (false) WITH CHECK (false);

-- credit_transactions: anon cannot access.
CREATE POLICY "credit_tx_deny_anon" ON credit_transactions
  FOR ALL TO anon
  USING (false) WITH CHECK (false);

-- tasks: anon cannot access.
CREATE POLICY "tasks_deny_anon" ON tasks
  FOR ALL TO anon
  USING (false) WITH CHECK (false);

-- smart_collections: anon cannot access.
CREATE POLICY "collections_deny_anon" ON smart_collections
  FOR ALL TO anon
  USING (false) WITH CHECK (false);

-- workspace_leads: anon cannot access.
CREATE POLICY "leads_deny_anon" ON workspace_leads
  FOR ALL TO anon
  USING (false) WITH CHECK (false);

-- global_intelligence_cache: anon can SELECT (no user-specific data),
-- but cannot INSERT/UPDATE/DELETE.
CREATE POLICY "cache_anon_readonly" ON global_intelligence_cache
  FOR SELECT TO anon
  USING (true);

CREATE POLICY "cache_deny_anon_write" ON global_intelligence_cache
  FOR INSERT TO anon
  WITH CHECK (false);

CREATE POLICY "cache_deny_anon_update" ON global_intelligence_cache
  FOR UPDATE TO anon
  USING (false) WITH CHECK (false);

CREATE POLICY "cache_deny_anon_delete" ON global_intelligence_cache
  FOR DELETE TO anon
  USING (false);

-- ============================================================
-- 14. UPDATED_AT TRIGGER
-- ============================================================
-- Auto-update updated_at on profiles, credit_balances, and tasks.
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_profiles_updated
  BEFORE UPDATE ON profiles
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_credit_balances_updated
  BEFORE UPDATE ON credit_balances
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_tasks_updated
  BEFORE UPDATE ON tasks
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ============================================================
-- DONE. The database is ready.
-- ============================================================
-- After running this schema:
--   1. The Clerk webhook calls handle_new_user() to create a profile +
--      credit_balances (50 free credits) + credit_transactions (signup_bonus).
--   2. When a user searches, the backend calls reserve_credits() to lock credits.
--   3. The worker runs the engine. On success it calls commit_credits() for the
--      actual spend and refund_credits() for any unused reserved amount.
--      On failure it calls refund_credits() for the full reserved amount.
--   4. The Paystack webhook calls add_credits() after payment verification.
--      This is idempotent — double webhooks do NOT double-credit.
--   5. The frontend polls GET /api/task/{task_id} for progress + current_step
--      to show an interactive progress bar (no WebSocket needed).
-- ============================================================
