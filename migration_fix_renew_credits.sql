-- ============================================================
-- BAD DECISION — MIGRATION: Fix renew_free_credits credit amount
-- ============================================================
-- Problem:
--   schema.sql defined renew_free_credits() to renew free users
--   with 50 credits, but the signup bonus is 100 and all marketing
--   copy says "100 free credits". The transaction log entry said
--   "100 free credits" but the actual UPDATE set the balance to 50.
--
-- This migration replaces the renew_free_credits function with the
-- corrected version that grants 100 credits on renewal.
--
-- Run this in the Supabase SQL Editor. Safe to run multiple times.
-- ============================================================

CREATE OR REPLACE FUNCTION renew_free_credits(p_user_id TEXT) RETURNS BOOLEAN AS $$
DECLARE
  cb_record RECORD;
  tier_val TEXT;
BEGIN
  SELECT cb.*, p.tier INTO cb_record
  FROM credit_balances cb
  JOIN profiles p ON p.id = cb.user_id
  WHERE cb.user_id = p_user_id
  FOR UPDATE;

  IF NOT FOUND THEN
    RETURN FALSE;
  END IF;

  tier_val := cb_record.tier;

  -- Check if credits have expired
  IF cb_record.credits_expiry IS NOT NULL AND cb_record.credits_expiry < now() THEN
    -- Credits have expired. Reset balance to 0.
    UPDATE credit_balances
    SET credits_balance = 0,
        credits_expiry = NULL,
        updated_at = now()
    WHERE user_id = p_user_id;

    -- For free tier users, grant 100 new credits with 30-day expiry.
    -- (Matches the signup bonus in handle_new_user — 100, not 50.)
    IF tier_val = 'free' THEN
      UPDATE credit_balances
      SET credits_balance = 100,
          credits_expiry = now() + interval '30 days',
          last_renewed_at = now(),
          updated_at = now()
      WHERE user_id = p_user_id;

      INSERT INTO credit_transactions (user_id, amount, transaction_type, description, reference_id)
      SELECT p_user_id, 100, 'signup_bonus', 'Monthly renewal: 100 free credits', 'renew_' || p_user_id || '_' || date_trunc('month', now())::text
      WHERE NOT EXISTS (
        SELECT 1 FROM credit_transactions
        WHERE reference_id = 'renew_' || p_user_id || '_' || date_trunc('month', now())::text
      );
    END IF;

    RETURN TRUE;
  END IF;

  -- Check if free user hasn't been renewed in 30+ days (even if expiry is NULL)
  IF tier_val = 'free' AND cb_record.last_renewed_at IS NOT NULL AND cb_record.last_renewed_at < now() - interval '30 days' THEN
    UPDATE credit_balances
    SET credits_balance = 100,
        credits_expiry = now() + interval '30 days',
        last_renewed_at = now(),
        updated_at = now()
    WHERE user_id = p_user_id;

    INSERT INTO credit_transactions (user_id, amount, transaction_type, description, reference_id)
    SELECT p_user_id, 100, 'signup_bonus', 'Monthly renewal: 100 free credits', 'renew_' || p_user_id || '_' || date_trunc('month', now())::text
    WHERE NOT EXISTS (
      SELECT 1 FROM credit_transactions
      WHERE reference_id = 'renew_' || p_user_id || '_' || date_trunc('month', now())::text
    );

    RETURN TRUE;
  END IF;

  RETURN FALSE;
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- Also fix any historical total_purchased inflation.
-- The original handle_new_user set total_purchased = 50 on signup,
-- which made every free user look like they'd bought 50 credits.
-- Reset total_purchased to 0 for any user whose only "purchase" was
-- the signup bonus (i.e., they have no 'purchase' type transactions).
-- ============================================================
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

-- Done.
