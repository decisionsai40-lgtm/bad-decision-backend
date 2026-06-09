-- ============================================================
-- BAD DECISION AI — Supabase Migration V8
-- ============================================================
-- Adds: subscription tier columns, location columns for tasks,
--       Paystack customer code, and coin transaction log table.
-- ============================================================

-- 1. Add subscription columns to profiles table
ALTER TABLE profiles
  ADD COLUMN IF NOT EXISTS subscription_tier TEXT DEFAULT 'free',
  ADD COLUMN IF NOT EXISTS subscription_id TEXT,
  ADD COLUMN IF NOT EXISTS subscription_status TEXT DEFAULT 'active'
    CHECK (subscription_status IN ('active', 'cancelled', 'expired')),
  ADD COLUMN IF NOT EXISTS paystack_customer_code TEXT;

-- Create index on subscription_tier for faster tier-based queries
CREATE INDEX IF NOT EXISTS idx_profiles_subscription_tier
  ON profiles (subscription_tier);

-- Create index on subscription_id for webhook lookups
CREATE INDEX IF NOT EXISTS idx_profiles_subscription_id
  ON profiles (subscription_id);

-- 2. Add location columns to tasks table
ALTER TABLE tasks
  ADD COLUMN IF NOT EXISTS continent TEXT DEFAULT '',
  ADD COLUMN IF NOT EXISTS country TEXT DEFAULT '',
  ADD COLUMN IF NOT EXISTS region TEXT DEFAULT '';

-- Create index on country for location-based task queries
CREATE INDEX IF NOT EXISTS idx_tasks_country
  ON tasks (country);

-- 3. Create coin_transactions log table
CREATE TABLE IF NOT EXISTS coin_transactions (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  amount INTEGER NOT NULL,  -- positive for credit, negative for debit
  transaction_type TEXT NOT NULL CHECK (transaction_type IN ('credit', 'debit')),
  reason TEXT NOT NULL,     -- e.g., 'subscription_pro', 'topup_small', 'search_deduction'
  reference TEXT,           -- Paystack reference or internal reference
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Create index on user_id for fast balance queries
CREATE INDEX IF NOT EXISTS idx_coin_transactions_user_id
  ON coin_transactions (user_id);

-- Create index on created_at for date-range queries
CREATE INDEX IF NOT EXISTS idx_coin_transactions_created_at
  ON coin_transactions (created_at);

-- Enable RLS on coin_transactions
ALTER TABLE coin_transactions ENABLE ROW LEVEL SECURITY;

-- RLS policy: users can only see their own transactions
CREATE POLICY "Users can view own coin transactions"
  ON coin_transactions
  FOR SELECT
  USING (auth.uid() = user_id);

-- Service role can do everything (backend uses service role key)
-- No additional policy needed since service role bypasses RLS

-- 4. Create get_coin_balance RPC function
CREATE OR REPLACE FUNCTION get_coin_balance(p_user_id UUID)
RETURNS INTEGER
LANGUAGE plpgsql
AS $$
DECLARE
  balance INTEGER;
BEGIN
  SELECT COALESCE(SUM(
    CASE
      WHEN transaction_type = 'credit' THEN amount
      WHEN transaction_type = 'debit' THEN -amount
      ELSE 0
    END
  ), 0) INTO balance
  FROM coin_transactions
  WHERE user_id = p_user_id;

  RETURN balance;
END;
$$;

-- 5. Update existing add_coins function to also log the transaction
CREATE OR REPLACE FUNCTION add_coins(p_user_id UUID, p_amount INTEGER)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
BEGIN
  -- Insert transaction record
  INSERT INTO coin_transactions (user_id, amount, transaction_type, reason)
  VALUES (p_user_id, p_amount, 'credit', 'manual_add');

  -- Update profile coin_balance if column exists
  BEGIN
    UPDATE profiles
    SET coin_balance = COALESCE(coin_balance, 0) + p_amount
    WHERE id = p_user_id;
  EXCEPTION WHEN undefined_column THEN
    NULL;  -- coin_balance column may not exist yet
  END;

  RETURN TRUE;
END;
$$;

-- 6. Update existing deduct_coins function to also log the transaction
CREATE OR REPLACE FUNCTION deduct_coins(p_user_id UUID, p_amount INTEGER)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
DECLARE
  current_balance INTEGER;
BEGIN
  -- Check current balance
  current_balance := get_coin_balance(p_user_id);

  IF current_balance < p_amount THEN
    RETURN FALSE;
  END IF;

  -- Insert debit transaction
  INSERT INTO coin_transactions (user_id, amount, transaction_type, reason)
  VALUES (p_user_id, p_amount, 'debit', 'search_deduction');

  -- Update profile coin_balance if column exists
  BEGIN
    UPDATE profiles
    SET coin_balance = COALESCE(coin_balance, 0) - p_amount
    WHERE id = p_user_id;
  EXCEPTION WHEN undefined_column THEN
    NULL;
  END;

  RETURN TRUE;
END;
$$;
