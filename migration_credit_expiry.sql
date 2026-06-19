-- ============================================================
-- BAD DECISION — CREDIT EXPIRY MIGRATION
-- ============================================================
-- Run this in Supabase SQL Editor to add credit expiry columns
-- and the renew_free_credits RPC function.
-- ============================================================

-- Add expiry columns to credit_balances
ALTER TABLE credit_balances ADD COLUMN IF NOT EXISTS credits_expiry TIMESTAMPTZ;
ALTER TABLE credit_balances ADD COLUMN IF NOT EXISTS last_renewed_at TIMESTAMPTZ DEFAULT now();

-- Set default expiry for existing users (30 days from now)
UPDATE credit_balances SET credits_expiry = now() + interval '30 days' WHERE credits_expiry IS NULL;
UPDATE credit_balances SET last_renewed_at = now() WHERE last_renewed_at IS NULL;

-- Update handle_new_user to set expiry
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

  INSERT INTO credit_balances (user_id, credits_balance, credits_reserved, total_purchased, credits_expiry, last_renewed_at)
  VALUES (p_clerk_id, 50, 0, 50, now() + interval '30 days', now())
  ON CONFLICT (user_id) DO NOTHING;

  INSERT INTO credit_transactions (user_id, amount, transaction_type, description, reference_id)
  SELECT p_clerk_id, 50, 'signup_bonus', '50 free credits for signing up', 'signup_' || p_clerk_id
  WHERE NOT EXISTS (
    SELECT 1 FROM credit_transactions
    WHERE reference_id = 'signup_' || p_clerk_id
      AND transaction_type = 'signup_bonus'
  );

  RETURN TRUE;
END;
$$ LANGUAGE plpgsql;

-- Update add_credits to set expiry on purchased credits
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
  IF p_amount <= 0 THEN RETURN FALSE; END IF;

  IF p_reference_id IS NOT NULL AND p_reference_id <> '' THEN
    SELECT COUNT(*) INTO already_exists
    FROM credit_transactions
    WHERE reference_id = p_reference_id AND transaction_type = p_transaction_type;
    IF already_exists > 0 THEN RETURN TRUE; END IF;
  END IF;

  UPDATE credit_balances
  SET credits_balance = credits_balance + p_amount,
      total_purchased = CASE WHEN p_transaction_type = 'purchase' THEN total_purchased + p_amount ELSE total_purchased END,
      credits_expiry = now() + interval '30 days',
      updated_at = now()
  WHERE user_id = p_user_id;

  GET DIAGNOSTICS row_count = ROW_COUNT;

  IF row_count = 0 THEN
    BEGIN
      INSERT INTO credit_balances (user_id, credits_balance, credits_reserved, total_purchased, credits_expiry, last_renewed_at)
      VALUES (p_user_id, p_amount, 0, CASE WHEN p_transaction_type = 'purchase' THEN p_amount ELSE 0 END, now() + interval '30 days', now());
    EXCEPTION WHEN OTHERS THEN
      UPDATE credit_balances SET credits_balance = credits_balance + p_amount, credits_expiry = now() + interval '30 days', updated_at = now() WHERE user_id = p_user_id;
    END;
  END IF;

  BEGIN
    INSERT INTO credit_transactions (user_id, amount, transaction_type, description, reference_id)
    SELECT p_user_id, p_amount, p_transaction_type, p_description, p_reference_id
    WHERE NOT EXISTS (SELECT 1 FROM credit_transactions WHERE reference_id = p_reference_id AND transaction_type = p_transaction_type);
  EXCEPTION WHEN OTHERS THEN NULL;
  END;

  RETURN TRUE;
END;
$$ LANGUAGE plpgsql;

-- New: renew_free_credits function (checks expiry, renews free credits monthly)
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

  IF NOT FOUND THEN RETURN FALSE; END IF;

  tier_val := cb_record.tier;

  IF cb_record.credits_expiry IS NOT NULL AND cb_record.credits_expiry < now() THEN
    UPDATE credit_balances SET credits_balance = 0, credits_expiry = NULL, updated_at = now() WHERE user_id = p_user_id;

    IF tier_val = 'free' THEN
      UPDATE credit_balances SET credits_balance = 50, credits_expiry = now() + interval '30 days', last_renewed_at = now(), updated_at = now() WHERE user_id = p_user_id;
      INSERT INTO credit_transactions (user_id, amount, transaction_type, description, reference_id)
      SELECT p_user_id, 50, 'signup_bonus', 'Monthly renewal: 50 free credits', 'renew_' || p_user_id || '_' || date_trunc('month', now())::text
      WHERE NOT EXISTS (SELECT 1 FROM credit_transactions WHERE reference_id = 'renew_' || p_user_id || '_' || date_trunc('month', now())::text);
    END IF;
    RETURN TRUE;
  END IF;

  IF tier_val = 'free' AND cb_record.last_renewed_at IS NOT NULL AND cb_record.last_renewed_at < now() - interval '30 days' THEN
    UPDATE credit_balances SET credits_balance = 50, credits_expiry = now() + interval '30 days', last_renewed_at = now(), updated_at = now() WHERE user_id = p_user_id;
    INSERT INTO credit_transactions (user_id, amount, transaction_type, description, reference_id)
    SELECT p_user_id, 50, 'signup_bonus', 'Monthly renewal: 50 free credits', 'renew_' || p_user_id || '_' || date_trunc('month', now())::text
    WHERE NOT EXISTS (SELECT 1 FROM credit_transactions WHERE reference_id = 'renew_' || p_user_id || '_' || date_trunc('month', now())::text);
    RETURN TRUE;
  END IF;

  RETURN FALSE;
END;
$$ LANGUAGE plpgsql;

-- Done. Credits now expire after 30 days. Free users get 50 renewed monthly.
