-- ============================================================
-- BAD DECISION — MIGRATION: Subscriptions Table
-- ============================================================
-- Tracks Paystack recurring subscriptions (monthly billing).
-- When a subscription is created, the user is charged monthly
-- and credits are auto-granted on each successful renewal.
--
-- RUN THIS IN SUPABASE SQL EDITOR.
-- Safe to run multiple times (uses IF NOT EXISTS).
-- ============================================================

CREATE TABLE IF NOT EXISTS subscriptions (
  id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id                   TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  plan_code                 TEXT NOT NULL,           -- Paystack plan code (PLN_xxx)
  tier                      TEXT NOT NULL,           -- 'starter' | 'growth' | 'pro'
  status                    TEXT NOT NULL DEFAULT 'active',  -- 'active' | 'canceled' | 'past_due' | 'trialing'
  paystack_customer_code    TEXT,                    -- CUS_xxx
  paystack_subscription_code TEXT,                   -- SUB_xxx
  paystack_email_token      TEXT,                    -- email token from Paystack
  current_period_end        TIMESTAMPTZ,             -- next billing date
  canceled_at               TIMESTAMPTZ,
  created_at                TIMESTAMPTZ DEFAULT now(),
  updated_at                TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_subscriptions_user ON subscriptions (user_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_status ON subscriptions (status) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_subscriptions_user_active ON subscriptions (user_id, status) WHERE status = 'active';

-- RLS — users can never directly access this table
ALTER TABLE subscriptions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "subscriptions_deny_anon" ON subscriptions;
CREATE POLICY "subscriptions_deny_anon" ON subscriptions
  FOR ALL TO anon USING (false) WITH CHECK (false);

-- ============================================================
-- DONE
-- ============================================================
-- Verification:
-- SELECT COUNT(*) FROM subscriptions;  -- should be 0 (no subscriptions yet)
