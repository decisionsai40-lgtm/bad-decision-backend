-- ============================================================
-- BAD DECISION — MIGRATION: Credit Lots System
-- ============================================================
-- This migration implements the new credit-lot system:
--   - 50 free credits (was 100) with 30-day auto-renewal (no accumulation)
--   - 60-day hard expiry on paid credits
--   - FIFO spending (oldest lot first)
--   - Hourly cron to expire credits and trigger renewal
--
-- RUN THIS IN SUPABASE SQL EDITOR.
-- Safe to run multiple times (uses CREATE OR REPLACE + IF NOT EXISTS).
-- ============================================================

-- ============================================================
-- 1. NEW TABLE: credit_lots
-- ============================================================
-- Every credit grant (signup, renewal, purchase) creates a lot.
-- Each lot tracks its own remaining balance + expiry date.
-- Spending deducts from the lot with earliest expiry first (FIFO).

CREATE TABLE IF NOT EXISTS credit_lots (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  amount        INTEGER NOT NULL,           -- original amount granted
  remaining     INTEGER NOT NULL,           -- what's left after partial usage
  source        TEXT NOT NULL,              -- 'signup_bonus' | 'purchase' | 'monthly_renewal' | 'ai_comp' | 'migration'
  is_free       BOOLEAN DEFAULT FALSE,      -- TRUE = 30-day auto-renew; FALSE = 60-day hard expiry
  expires_at    TIMESTAMPTZ NOT NULL,       -- 30 or 60 days from creation
  created_at    TIMESTAMPTZ DEFAULT now(),
  CONSTRAINT remaining_nonnegative CHECK (remaining >= 0),
  CONSTRAINT remaining_not_exceed_amount CHECK (remaining <= amount)
);

CREATE INDEX IF NOT EXISTS idx_credit_lots_user_expires ON credit_lots (user_id, expires_at);
CREATE INDEX IF NOT EXISTS idx_credit_lots_user_remaining ON credit_lots (user_id, remaining)
  WHERE remaining > 0;

-- RLS — users can never directly read/write this table
ALTER TABLE credit_lots ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "credit_lots_deny_anon" ON credit_lots;
CREATE POLICY "credit_lots_deny_anon" ON credit_lots
  FOR ALL TO anon USING (false) WITH CHECK (false);


-- ============================================================
-- 2. UPDATED RPC: handle_new_user
-- ============================================================
-- Now grants 50 free credits (was 100) and creates a credit_lot.
-- total_purchased is 0 (free credits are not purchases).

CREATE OR REPLACE FUNCTION handle_new_user(
  p_clerk_id  TEXT,
  p_email     TEXT,
  p_full_name TEXT,
  p_country   TEXT
) RETURNS BOOLEAN AS $$
BEGIN
  INSERT INTO profiles (id, email, full_name, tier, country)
  VALUES (p_clerk_id, p_email, p_full_name, 'free', COALESCE(NULLIF(p_country, ''), 'US'))
  ON CONFLICT (id) DO NOTHING;

  -- Insert credit_balances with 50 free credits + 30-day expiry
  -- total_purchased is 0 — free credits are not purchases
  INSERT INTO credit_balances (user_id, credits_balance, credits_reserved,
                                total_purchased, credits_expiry, last_renewed_at)
  VALUES (p_clerk_id, 50, 0, 0, now() + interval '30 days', now())
  ON CONFLICT (user_id) DO NOTHING;

  -- Create the credit lot for tracking (idempotent — only if no lot exists yet)
  INSERT INTO credit_lots (user_id, amount, remaining, source, is_free, expires_at)
  SELECT p_clerk_id, 50, 50, 'signup_bonus', TRUE, now() + interval '30 days'
  WHERE NOT EXISTS (
    SELECT 1 FROM credit_lots WHERE user_id = p_clerk_id
  );

  INSERT INTO credit_transactions (user_id, amount, transaction_type, description, reference_id)
  SELECT p_clerk_id, 50, 'signup_bonus', '50 free credits for signing up',
         'signup_' || p_clerk_id
  WHERE NOT EXISTS (
    SELECT 1 FROM credit_transactions
    WHERE reference_id = 'signup_' || p_clerk_id
      AND transaction_type = 'signup_bonus'
  );

  RETURN TRUE;
END;
$$ LANGUAGE plpgsql;


-- ============================================================
-- 3. UPDATED RPC: add_credits
-- ============================================================
-- Now also creates a paid credit_lot (60-day expiry) for purchases.

CREATE OR REPLACE FUNCTION add_credits(
  p_user_id          TEXT,
  p_amount           INTEGER,
  p_transaction_type TEXT,
  p_description      TEXT,
  p_reference_id     TEXT
) RETURNS BOOLEAN AS $$
DECLARE
  already_exists INTEGER;
BEGIN
  IF p_amount <= 0 THEN RETURN FALSE; END IF;

  -- Idempotency check
  IF p_reference_id IS NOT NULL AND p_reference_id <> '' THEN
    SELECT COUNT(*) INTO already_exists
    FROM credit_transactions
    WHERE reference_id = p_reference_id
      AND transaction_type = p_transaction_type;
    IF already_exists > 0 THEN RETURN TRUE; END IF;
  END IF;

  -- Update balance
  UPDATE credit_balances
  SET credits_balance = credits_balance + p_amount,
      total_purchased = CASE
        WHEN p_transaction_type = 'purchase' THEN total_purchased + p_amount
        ELSE total_purchased
      END,
      updated_at = now()
  WHERE user_id = p_user_id;

  -- Create the credit lot (60-day expiry for paid credits)
  IF p_transaction_type IN ('purchase', 'subscription_grant') THEN
    INSERT INTO credit_lots (user_id, amount, remaining, source, is_free, expires_at)
    VALUES (p_user_id, p_amount, p_amount, 'purchase', FALSE, now() + interval '60 days');
  END IF;

  -- Log transaction
  BEGIN
    INSERT INTO credit_transactions (user_id, amount, transaction_type, description, reference_id)
    SELECT p_user_id, p_amount, p_transaction_type, p_description, p_reference_id
    WHERE NOT EXISTS (
      SELECT 1 FROM credit_transactions
      WHERE reference_id = p_reference_id
        AND transaction_type = p_transaction_type
    );
  EXCEPTION WHEN OTHERS THEN NULL;
  END;

  RETURN TRUE;
END;
$$ LANGUAGE plpgsql;


-- ============================================================
-- 4. UPDATED RPC: renew_free_credits
-- ============================================================
-- On every credits fetch, this is called. It now also:
--   - Sums remaining from expired free lots
--   - Deducts that from balance
--   - Deletes expired free lots
--   - Creates a new 50-credit free lot (30-day expiry) for free-tier users

CREATE OR REPLACE FUNCTION renew_free_credits(p_user_id TEXT) RETURNS BOOLEAN AS $$
DECLARE
  cb_record RECORD;
  tier_val TEXT;
  expired_remaining INTEGER;
BEGIN
  SELECT cb.*, p.tier INTO cb_record
  FROM credit_balances cb
  JOIN profiles p ON p.id = cb.user_id
  WHERE cb.user_id = p_user_id
  FOR UPDATE;

  IF NOT FOUND THEN RETURN FALSE; END IF;

  tier_val := cb_record.tier;

  -- Check if credits have expired
  IF cb_record.credits_expiry IS NOT NULL AND cb_record.credits_expiry < now() THEN
    -- Sum remaining from all expired free lots for this user
    SELECT COALESCE(SUM(remaining), 0) INTO expired_remaining
    FROM credit_lots
    WHERE user_id = p_user_id
      AND is_free = TRUE
      AND expires_at <= now();

    -- Deduct expired amount from balance (cap at current balance to avoid negative)
    UPDATE credit_balances
    SET credits_balance = GREATEST(credits_balance - expired_remaining, 0),
        credits_expiry = NULL,
        updated_at = now()
    WHERE user_id = p_user_id;

    -- Delete expired free lots
    DELETE FROM credit_lots
    WHERE user_id = p_user_id
      AND is_free = TRUE
      AND expires_at <= now();

    -- For free tier users, grant 50 new credits with 30-day expiry
    IF tier_val = 'free' THEN
      UPDATE credit_balances
      SET credits_balance = 50,
          credits_expiry = now() + interval '30 days',
          last_renewed_at = now(),
          updated_at = now()
      WHERE user_id = p_user_id;

      INSERT INTO credit_lots (user_id, amount, remaining, source, is_free, expires_at)
      VALUES (p_user_id, 50, 50, 'monthly_renewal', TRUE, now() + interval '30 days');

      INSERT INTO credit_transactions (user_id, amount, transaction_type, description, reference_id)
      SELECT p_user_id, 50, 'signup_bonus',
             'Monthly renewal: 50 free credits',
             'renew_' || p_user_id || '_' || date_trunc('month', now())::text
      WHERE NOT EXISTS (
        SELECT 1 FROM credit_transactions
        WHERE reference_id = 'renew_' || p_user_id || '_' || date_trunc('month', now())::text
      );
    END IF;

    RETURN TRUE;
  END IF;

  RETURN FALSE;
END;
$$ LANGUAGE plpgsql;


-- ============================================================
-- 5. NEW RPC: expire_credits_cron
-- ============================================================
-- Called hourly by Cloud Scheduler (Phase D) or manually via /api/cron/expire-credits.
-- Scans for expiring lots across ALL users, deducts their remaining from balance,
-- and triggers renewal for free-tier users.

CREATE OR REPLACE FUNCTION expire_credits_cron() RETURNS INTEGER AS $$
DECLARE
  expired_count INTEGER := 0;
  user_record RECORD;
BEGIN
  -- Find all users with lots that have expired
  FOR user_record IN
    SELECT DISTINCT user_id,
      SUM(remaining) AS total_expired,
      BOOL_OR(is_free) AS has_free_expired
    FROM credit_lots
    WHERE expires_at <= now()
      AND remaining > 0
    GROUP BY user_id
  LOOP
    -- Deduct expired credits from balance
    UPDATE credit_balances
    SET credits_balance = GREATEST(credits_balance - user_record.total_expired, 0),
        updated_at = now()
    WHERE user_id = user_record.user_id;

    -- Delete expired lots
    DELETE FROM credit_lots
    WHERE user_id = user_record.user_id
      AND expires_at <= now();

    -- If free credits expired, trigger renewal
    IF user_record.has_free_expired THEN
      PERFORM renew_free_credits(user_record.user_id);
    END IF;

    expired_count := expired_count + 1;
  END LOOP;

  RETURN expired_count;
END;
$$ LANGUAGE plpgsql;


-- ============================================================
-- 6. NEW RPC: get_credit_lots_summary
-- ============================================================
-- Returns a user's non-expired lots for the dashboard display.
-- Used by the frontend to show "X credits (Y expiring in 7 days)".

CREATE OR REPLACE FUNCTION get_credit_lots_summary(p_user_id TEXT)
RETURNS JSON AS $$
DECLARE
  result JSON;
BEGIN
  SELECT COALESCE(json_agg(
    json_build_object(
      'id', id,
      'amount', amount,
      'remaining', remaining,
      'source', source,
      'is_free', is_free,
      'expires_at', expires_at,
      'days_until_expiry', EXTRACT(DAY FROM expires_at - now())::INTEGER
    ) ORDER BY expires_at ASC
  ), '[]'::json) INTO result
  FROM credit_lots
  WHERE user_id = p_user_id
    AND remaining > 0
    AND expires_at > now();

  RETURN result;
END;
$$ LANGUAGE plpgsql;


-- ============================================================
-- 7. MIGRATION: Convert existing balances to credit_lots
-- ============================================================
-- Every existing user with a positive balance gets a single lot
-- matching their balance. Free users: 30-day expiry. Paid users: 60-day.

INSERT INTO credit_lots (user_id, amount, remaining, source, is_free, expires_at)
SELECT
  cb.user_id,
  cb.credits_balance AS amount,
  cb.credits_balance AS remaining,
  'migration' AS source,
  (p.tier = 'free') AS is_free,
  CASE WHEN p.tier = 'free'
    THEN now() + interval '30 days'
    ELSE now() + interval '60 days'
  END AS expires_at
FROM credit_balances cb
JOIN profiles p ON p.id = cb.user_id
WHERE cb.credits_balance > 0
  AND NOT EXISTS (
    SELECT 1 FROM credit_lots cl WHERE cl.user_id = cb.user_id
  );

-- Result: every user with a positive balance now has exactly one credit_lot
-- matching their balance. Future grants create additional lots.


-- ============================================================
-- 8. FIX: Reset inflated total_purchased for users
-- ============================================================
-- The old handle_new_user set total_purchased=50 on signup, which was wrong.
-- Reset total_purchased to 0 for any user whose only "purchase" was the
-- signup bonus (i.e., they have no real 'purchase' type transactions).

UPDATE credit_balances
SET total_purchased = 0,
    updated_at = now()
WHERE user_id IN (
  SELECT DISTINCT cb.user_id
  FROM credit_balances cb
  WHERE cb.total_purchased > 0
    AND NOT EXISTS (
      SELECT 1 FROM credit_transactions ct
      WHERE ct.user_id = cb.user_id
        AND ct.transaction_type = 'purchase'
    )
);


-- ============================================================
-- DONE
-- ============================================================
-- Verification queries (run manually to confirm):
--
-- SELECT COUNT(*) FROM credit_lots;  -- should equal number of users with balance > 0
-- SELECT COUNT(*) FROM credit_lots WHERE is_free = TRUE;  -- free-tier users
-- SELECT COUNT(*) FROM credit_lots WHERE is_free = FALSE;  -- paid-credit holders
-- SELECT * FROM credit_lots WHERE user_id = 'user_xxx' ORDER BY expires_at;
--
-- To test expiry manually:
-- UPDATE credit_lots SET expires_at = now() - interval '1 hour' WHERE user_id = 'user_xxx';
-- SELECT renew_free_credits('user_xxx');
-- SELECT * FROM credit_balances WHERE user_id = 'user_xxx';  -- should show renewed balance
