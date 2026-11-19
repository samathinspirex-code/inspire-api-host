-- Optional CMS-managed payment plans per course; existing courses stay hidden (disabled).
ALTER TABLE academic_courses
  ADD COLUMN IF NOT EXISTS payment_plans_enabled BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS payment_plans JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE programs
  ADD COLUMN IF NOT EXISTS payment_plans_enabled BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS payment_plans JSONB NOT NULL DEFAULT '{}'::jsonb;
