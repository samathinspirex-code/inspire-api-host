-- Inspire Brain Accounts: core schema (PostgreSQL 14+)
-- Double-entry ledger with database-level guarantees:
--   * every journal balances (deferred constraint trigger)
--   * posted journals are immutable (corrections are reversals)
--   * no posting into a locked month

CREATE SCHEMA IF NOT EXISTS payments;
SET LOCAL search_path TO payments, pg_catalog;

DO $$ BEGIN
  CREATE TYPE account_type AS ENUM ('asset','contra_asset','liability','equity','income','cost_of_delivery','expense');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now());

-- ---------- Users & audit ----------

CREATE TABLE IF NOT EXISTS audit_log (
  id bigserial PRIMARY KEY,
  at timestamptz NOT NULL DEFAULT now(),
  actor text NOT NULL,
  action text NOT NULL,
  entity text NOT NULL,
  entity_id text,
  detail jsonb
);

CREATE TABLE IF NOT EXISTS settings (key text PRIMARY KEY, value jsonb NOT NULL, updated_at timestamptz NOT NULL DEFAULT now());

-- ---------- Ledger ----------
CREATE TABLE IF NOT EXISTS accounts (
  code text PRIMARY KEY,
  name text NOT NULL,
  type account_type NOT NULL,
  grp text NOT NULL CHECK (grp IN ('CA','NCA','CL','NCL','EQ','REV','COD','OPEX')),
  is_system boolean NOT NULL DEFAULT false,
  is_active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS fiscal_periods (
  period_start date PRIMARY KEY CHECK (extract(day FROM period_start) = 1),
  status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','locked')),
  locked_at timestamptz,
  locked_by text
);

CREATE SEQUENCE IF NOT EXISTS journal_number_seq;

CREATE TABLE IF NOT EXISTS journals (
  id bigserial PRIMARY KEY,
  number text UNIQUE NOT NULL,
  entry_date date NOT NULL,
  memo text NOT NULL,
  source text NOT NULL,             -- manual, billing, payhere, receipt, bank, bills, close, year_end, migration, reversal
  source_ref text,
  cash_flow_category text,
  idempotency_key text UNIQUE,
  reversal_of bigint REFERENCES journals(id),
  created_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS journals_date_idx ON journals(entry_date);

-- ---------- Learners, programmes, enrolments ----------
CREATE TABLE IF NOT EXISTS programmes (
  code text PRIMARY KEY,
  name text NOT NULL,
  revenue_account text NOT NULL REFERENCES accounts(code),
  teaching_months int NOT NULL CHECK (teaching_months > 0),
  default_fee numeric(14,2),
  default_instalments int NOT NULL DEFAULT 1,
  active boolean NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS learners (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  learner_no text UNIQUE NOT NULL,
  first_name text NOT NULL,
  last_name text NOT NULL,
  email text,
  phone text,
  address text,
  city text DEFAULT 'Colombo',
  lms_ref text UNIQUE,               -- id in the Inspire LMS, for sync
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE SEQUENCE IF NOT EXISTS learner_number_seq START 1000;

CREATE TABLE IF NOT EXISTS enrolments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  learner_id uuid NOT NULL REFERENCES learners(id),
  programme_code text NOT NULL REFERENCES programmes(code),
  start_date date NOT NULL,
  total_fee numeric(14,2) NOT NULL CHECK (total_fee >= 0),
  instalments int NOT NULL CHECK (instalments > 0),
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','completed','withdrawn')),
  withdrawn_on date,
  counsellor text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS journal_lines (
  id bigserial PRIMARY KEY,
  journal_id bigint NOT NULL REFERENCES journals(id),
  account_code text NOT NULL REFERENCES accounts(code),
  debit numeric(14,2) NOT NULL DEFAULT 0 CHECK (debit >= 0),
  credit numeric(14,2) NOT NULL DEFAULT 0 CHECK (credit >= 0),
  learner_id uuid REFERENCES learners(id),
  enrolment_id uuid REFERENCES enrolments(id),
  CHECK (debit = 0 OR credit = 0),
  CHECK (debit + credit > 0)
);
CREATE INDEX IF NOT EXISTS jl_journal_idx ON journal_lines(journal_id);
CREATE INDEX IF NOT EXISTS jl_account_idx ON journal_lines(account_code);
CREATE INDEX IF NOT EXISTS jl_enrolment_idx ON journal_lines(enrolment_id);

CREATE TABLE IF NOT EXISTS instalments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  enrolment_id uuid NOT NULL REFERENCES enrolments(id),
  seq int NOT NULL,
  due_date date NOT NULL,
  amount numeric(14,2) NOT NULL CHECK (amount > 0),
  status text NOT NULL DEFAULT 'scheduled' CHECK (status IN ('scheduled','invoiced','paid','cancelled')),
  invoice_id uuid,
  UNIQUE (enrolment_id, seq)
);

CREATE SEQUENCE IF NOT EXISTS invoice_number_seq START 10000;
CREATE TABLE IF NOT EXISTS invoices (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  number text UNIQUE NOT NULL,
  learner_id uuid NOT NULL REFERENCES learners(id),
  enrolment_id uuid REFERENCES enrolments(id),
  instalment_id uuid REFERENCES instalments(id),
  kind text NOT NULL CHECK (kind IN ('registration','instalment','course','late_fee','other')),
  description text NOT NULL,
  issue_date date NOT NULL,
  due_date date NOT NULL,
  amount numeric(14,2) NOT NULL CHECK (amount > 0),
  amount_paid numeric(14,2) NOT NULL DEFAULT 0,
  status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','paid','cancelled')),
  journal_id bigint REFERENCES journals(id),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS invoices_learner_idx ON invoices(learner_id);

CREATE TABLE IF NOT EXISTS payment_links (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code text UNIQUE NOT NULL,                       -- PL-2050, shown to staff and learners
  token text UNIQUE NOT NULL,                      -- unguessable, used in the public URL
  invoice_id uuid NOT NULL REFERENCES invoices(id),
  learner_id uuid NOT NULL REFERENCES learners(id),
  amount numeric(14,2) NOT NULL CHECK (amount > 0),
  currency text NOT NULL DEFAULT 'LKR',
  purpose text NOT NULL,
  channel text NOT NULL CHECK (channel IN ('whatsapp','email','sms')),
  status text NOT NULL DEFAULT 'sent' CHECK (status IN ('sent','paid','expired','cancelled')),
  due_date date NOT NULL,
  expires_at timestamptz,
  reminders_sent int NOT NULL DEFAULT 0,
  last_reminder_at timestamptz,
  checkout_attempts int NOT NULL DEFAULT 0,
  paid_at timestamptz,
  created_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE SEQUENCE IF NOT EXISTS payment_link_seq START 2050;

CREATE TABLE IF NOT EXISTS gateway_events (
  id bigserial PRIMARY KEY,
  provider text NOT NULL DEFAULT 'payhere',
  order_id text NOT NULL,
  payment_id text,
  status_code int,
  amount numeric(14,2),
  currency text,
  method text,
  signature_valid boolean NOT NULL,
  outcome text,                                    -- posted, duplicate, ignored, rejected
  raw jsonb NOT NULL,
  received_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS gateway_events_posted_uq ON gateway_events(provider, payment_id) WHERE outcome = 'posted';

CREATE TABLE IF NOT EXISTS payments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  invoice_id uuid REFERENCES invoices(id),
  learner_id uuid NOT NULL REFERENCES learners(id),
  amount numeric(14,2) NOT NULL CHECK (amount > 0),
  method text NOT NULL CHECK (method IN ('payhere','bank_transfer','cash')),
  gateway_payment_id text UNIQUE,
  received_date date NOT NULL,
  journal_id bigint NOT NULL REFERENCES journals(id),
  created_at timestamptz NOT NULL DEFAULT now()
);

-- ---------- Revenue recognition, assets, close ----------
CREATE TABLE IF NOT EXISTS revenue_recognition (
  enrolment_id uuid NOT NULL REFERENCES enrolments(id),
  period_start date NOT NULL,
  amount numeric(14,2) NOT NULL,
  journal_id bigint NOT NULL REFERENCES journals(id),
  PRIMARY KEY (enrolment_id, period_start)
);

CREATE TABLE IF NOT EXISTS fixed_assets (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  cost numeric(14,2) NOT NULL CHECK (cost > 0),
  salvage numeric(14,2) NOT NULL DEFAULT 0,
  acquired_on date NOT NULL,
  useful_life_months int NOT NULL CHECK (useful_life_months > 0),
  opening_depreciation numeric(14,2) NOT NULL DEFAULT 0,  -- accumulated depreciation brought forward at migration
  disposed_on date
);

CREATE TABLE IF NOT EXISTS depreciation_runs (
  asset_id uuid NOT NULL REFERENCES fixed_assets(id),
  period_start date NOT NULL,
  amount numeric(14,2) NOT NULL,
  journal_id bigint NOT NULL REFERENCES journals(id),
  PRIMARY KEY (asset_id, period_start)
);

CREATE TABLE IF NOT EXISTS close_tasks (
  period_start date NOT NULL,
  task_key text NOT NULL,
  done boolean NOT NULL DEFAULT false,
  done_by text,
  done_at timestamptz,
  PRIMARY KEY (period_start, task_key)
);

-- ---------- Payables ----------
CREATE TABLE IF NOT EXISTS suppliers (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  kind text NOT NULL DEFAULT 'vendor' CHECK (kind IN ('lecturer','vendor','landlord','awarding_body','other')),
  email text, phone text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE SEQUENCE IF NOT EXISTS bill_number_seq START 500;
CREATE TABLE IF NOT EXISTS bills (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  number text UNIQUE NOT NULL,
  supplier_id uuid NOT NULL REFERENCES suppliers(id),
  supplier_ref text,
  bill_date date NOT NULL,
  due_date date NOT NULL,
  expense_account text NOT NULL REFERENCES accounts(code),
  description text NOT NULL,
  amount numeric(14,2) NOT NULL CHECK (amount > 0),
  amount_paid numeric(14,2) NOT NULL DEFAULT 0,
  status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','paid','cancelled')),
  journal_id bigint REFERENCES journals(id),
  created_at timestamptz NOT NULL DEFAULT now()
);

-- ---------- Banking ----------
CREATE TABLE IF NOT EXISTS bank_statement_lines (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  bank_account text NOT NULL REFERENCES accounts(code),
  txn_date date NOT NULL,
  description text NOT NULL,
  amount numeric(14,2) NOT NULL,                  -- + money in, - money out
  external_ref text NOT NULL DEFAULT '',
  status text NOT NULL DEFAULT 'unmatched' CHECK (status IN ('unmatched','matched','ignored')),
  journal_id bigint REFERENCES journals(id),
  imported_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (bank_account, txn_date, amount, description, external_ref)
);

-- ---------- Messaging ----------
CREATE TABLE IF NOT EXISTS message_outbox (
  id bigserial PRIMARY KEY,
  channel text NOT NULL CHECK (channel IN ('whatsapp','email','sms')),
  to_addr text NOT NULL,
  subject text,
  body text NOT NULL,
  template text,
  payment_link_id uuid REFERENCES payment_links(id),
  status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','sent','failed','skipped')),
  attempts int NOT NULL DEFAULT 0,
  error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  sent_at timestamptz
);

-- =====================================================================
-- Ledger guarantees
-- =====================================================================

-- 1. Every journal balances (checked at COMMIT, so lines can be inserted one by one).
CREATE OR REPLACE FUNCTION ib_check_journal_balanced() RETURNS trigger AS $$
DECLARE jid bigint; d numeric; c numeric; n int;
BEGIN
  jid := COALESCE(NEW.journal_id, OLD.journal_id);
  SELECT COALESCE(SUM(debit),0), COALESCE(SUM(credit),0), COUNT(*) INTO d, c, n FROM journal_lines WHERE journal_id = jid;
  IF n < 2 OR d <> c THEN
    RAISE EXCEPTION 'Journal % does not balance (debits %, credits %, lines %)', jid, d, c, n USING ERRCODE = 'check_violation';
  END IF;
  RETURN NULL;
END $$ LANGUAGE plpgsql SET search_path = payments, pg_catalog;

DROP TRIGGER IF EXISTS trg_journal_balanced ON journal_lines;
CREATE CONSTRAINT TRIGGER trg_journal_balanced AFTER INSERT ON journal_lines
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ib_check_journal_balanced();

-- 2. Posted entries are immutable. Corrections are made with reversing journals.
CREATE OR REPLACE FUNCTION ib_block_change() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'Posted ledger entries cannot be changed or deleted; post a reversal instead' USING ERRCODE = 'insufficient_privilege';
END $$ LANGUAGE plpgsql SET search_path = payments, pg_catalog;

DROP TRIGGER IF EXISTS trg_journals_immutable ON journals;
CREATE TRIGGER trg_journals_immutable BEFORE UPDATE OR DELETE ON journals FOR EACH ROW EXECUTE FUNCTION ib_block_change();
DROP TRIGGER IF EXISTS trg_lines_immutable ON journal_lines;
CREATE TRIGGER trg_lines_immutable BEFORE UPDATE OR DELETE ON journal_lines FOR EACH ROW EXECUTE FUNCTION ib_block_change();

-- 3. No posting into a locked month.
CREATE OR REPLACE FUNCTION ib_check_period_open() RETURNS trigger AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM fiscal_periods WHERE period_start = date_trunc('month', NEW.entry_date)::date AND status = 'locked') THEN
    RAISE EXCEPTION 'Period % is locked', to_char(NEW.entry_date, 'Mon YYYY') USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql SET search_path = payments, pg_catalog;

DROP TRIGGER IF EXISTS trg_journals_period_open ON journals;
CREATE TRIGGER trg_journals_period_open BEFORE INSERT ON journals FOR EACH ROW EXECUTE FUNCTION ib_check_period_open();

-- Convenience view: account balances (natural sign) across all time.
CREATE OR REPLACE VIEW v_account_balances AS
SELECT a.code, a.name, a.type, a.grp,
       COALESCE(SUM(l.debit),0) AS debit, COALESCE(SUM(l.credit),0) AS credit,
       CASE WHEN a.type IN ('asset','cost_of_delivery','expense') THEN COALESCE(SUM(l.debit - l.credit),0)
            ELSE COALESCE(SUM(l.credit - l.debit),0) END AS balance
FROM accounts a LEFT JOIN journal_lines l ON l.account_code = a.code
GROUP BY a.code, a.name, a.type, a.grp;

CREATE TABLE IF NOT EXISTS module_state (id boolean PRIMARY KEY DEFAULT true CHECK(id), demo_data boolean NOT NULL DEFAULT false);
ALTER TABLE invoices ADD COLUMN IF NOT EXISTS cancelled_on date;
ALTER TABLE journals ADD COLUMN IF NOT EXISTS posting_xid xid8 NOT NULL DEFAULT pg_current_xact_id();
CREATE OR REPLACE FUNCTION guard_late_journal_lines() RETURNS trigger AS $$
BEGIN
IF NOT EXISTS(SELECT 1 FROM journals WHERE id=NEW.journal_id AND posting_xid=pg_current_xact_id()) THEN
RAISE EXCEPTION 'Lines cannot be appended to a committed journal' USING ERRCODE='insufficient_privilege';
END IF;
RETURN NEW;
END $$ LANGUAGE plpgsql SET search_path=payments,pg_catalog;
DROP TRIGGER IF EXISTS trg_no_late_lines ON journal_lines;
CREATE TRIGGER trg_no_late_lines BEFORE INSERT ON journal_lines FOR EACH ROW EXECUTE FUNCTION guard_late_journal_lines();
INSERT INTO module_state(id) VALUES(true) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS checkout_orders (order_id text PRIMARY KEY, link_id uuid NOT NULL REFERENCES payment_links(id), amount numeric(14,2) NOT NULL, created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS request_keys (key text PRIMARY KEY, actor text NOT NULL, fingerprint text NOT NULL, response jsonb NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_link_per_invoice ON payment_links(invoice_id) WHERE status = 'sent';
CREATE UNIQUE INDEX IF NOT EXISTS one_reversal_per_journal ON journals(reversal_of) WHERE reversal_of IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS one_bank_match_per_journal ON bank_statement_lines(journal_id) WHERE journal_id IS NOT NULL;
-- A journal header with no lines must also fail at commit.
CREATE OR REPLACE FUNCTION check_header_balanced() RETURNS trigger AS $$
DECLARE d numeric; c numeric; n int;
BEGIN
SELECT COALESCE(SUM(debit),0),COALESCE(SUM(credit),0),COUNT(*) INTO d,c,n FROM journal_lines WHERE journal_id=NEW.id;
IF n<2 OR d<>c THEN RAISE EXCEPTION 'Journal must have at least two balanced lines' USING ERRCODE='check_violation'; END IF;
RETURN NULL;
END $$ LANGUAGE plpgsql SET search_path=payments,pg_catalog;
DROP TRIGGER IF EXISTS trg_header_balanced ON journals;
CREATE CONSTRAINT TRIGGER trg_header_balanced AFTER INSERT ON journals DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_header_balanced();
