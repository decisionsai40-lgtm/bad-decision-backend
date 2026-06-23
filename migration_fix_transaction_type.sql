-- ============================================================
-- BAD DECISION — Fix credit_transactions CHECK constraint
-- ============================================================
-- The credit_transactions table has a CHECK constraint that only allows
-- specific transaction_type values: signup_bonus, purchase, search_debit,
-- refund, tier_upgrade, reserve, commit.
--
-- But deduct_credits_fifo() inserts transaction_type = 'spend' which
-- violates this constraint, causing every credit deduction to fail
-- with: 'new row for relation "credit_transactions" violates check
-- constraint "credit_transactions_transaction_type_check"'
--
-- This migration adds 'spend' to the allowed values.
-- ============================================================

-- Drop the old constraint and add a new one with 'spend' included
ALTER TABLE credit_transactions DROP CONSTRAINT IF EXISTS credit_transactions_transaction_type_check;

ALTER TABLE credit_transactions ADD CONSTRAINT credit_transactions_transaction_type_check
  CHECK (transaction_type IN (
    'signup_bonus', 'purchase', 'search_debit', 'refund',
    'tier_upgrade', 'reserve', 'commit', 'spend',
    'message_gen_single', 'message_gen_batch', 'message_regenerate',
    'email_send', 'ai_turn'
  ));
