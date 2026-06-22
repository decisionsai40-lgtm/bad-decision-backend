-- ============================================================
-- BAD DECISION — MIGRATION: Engine Overhaul (Phase C3)
-- ============================================================
-- Adds columns for:
--   - Messaging platform detection (WhatsApp + Telegram)
--   - Ecommerce engine fields (platform, products, tech stack)
--   - Ads engine fields (ad platforms, spend estimate)
--   - Companies engine enrichment (NAICS, officers)
--
-- RUN THIS IN SUPABASE SQL EDITOR.
-- Safe to run multiple times (uses IF NOT EXISTS).
-- ============================================================

-- Messaging platform columns
ALTER TABLE workspace_leads ADD COLUMN IF NOT EXISTS is_whatsapp BOOLEAN DEFAULT FALSE;
ALTER TABLE workspace_leads ADD COLUMN IF NOT EXISTS is_telegram BOOLEAN DEFAULT FALSE;
ALTER TABLE workspace_leads ADD COLUMN IF NOT EXISTS messaging_checked BOOLEAN DEFAULT FALSE;

-- Ecommerce fields
ALTER TABLE workspace_leads ADD COLUMN IF NOT EXISTS ecommerce_platform TEXT;
ALTER TABLE workspace_leads ADD COLUMN IF NOT EXISTS product_count INTEGER;
ALTER TABLE workspace_leads ADD COLUMN IF NOT EXISTS product_categories TEXT[];
ALTER TABLE workspace_leads ADD COLUMN IF NOT EXISTS average_price TEXT;
ALTER TABLE workspace_leads ADD COLUMN IF NOT EXISTS price_range TEXT;
ALTER TABLE workspace_leads ADD COLUMN IF NOT EXISTS store_currency TEXT;
ALTER TABLE workspace_leads ADD COLUMN IF NOT EXISTS estimated_revenue TEXT;
ALTER TABLE workspace_leads ADD COLUMN IF NOT EXISTS tech_stack TEXT[];
ALTER TABLE workspace_leads ADD COLUMN IF NOT EXISTS uses_email_marketing BOOLEAN;
ALTER TABLE workspace_leads ADD COLUMN IF NOT EXISTS uses_ad_tracking BOOLEAN;
ALTER TABLE workspace_leads ADD COLUMN IF NOT EXISTS uses_subscriptions BOOLEAN;
ALTER TABLE workspace_leads ADD COLUMN IF NOT EXISTS store_age_days INTEGER;
ALTER TABLE workspace_leads ADD COLUMN IF NOT EXISTS social_media_links TEXT[];

-- Ads engine fields
ALTER TABLE workspace_leads ADD COLUMN IF NOT EXISTS ad_platforms TEXT[];
ALTER TABLE workspace_leads ADD COLUMN IF NOT EXISTS ad_start_date TEXT;
ALTER TABLE workspace_leads ADD COLUMN IF NOT EXISTS ad_creative_url TEXT;
ALTER TABLE workspace_leads ADD COLUMN IF NOT EXISTS estimated_monthly_ad_spend TEXT;

-- Companies engine enrichment fields
ALTER TABLE workspace_leads ADD COLUMN IF NOT EXISTS naics_code TEXT;
ALTER TABLE workspace_leads ADD COLUMN IF NOT EXISTS naics_description TEXT;
ALTER TABLE workspace_leads ADD COLUMN IF NOT EXISTS business_start_date TEXT;
ALTER TABLE workspace_leads ADD COLUMN IF NOT EXISTS company_officers TEXT;

-- Done. Verify:
-- SELECT column_name FROM information_schema.columns WHERE table_name = 'workspace_leads' ORDER BY ordinal_position;
