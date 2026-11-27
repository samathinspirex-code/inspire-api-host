-- New website (inspire-web-v2) on the shared API. Additive and safe to rerun: no existing table is changed
-- except two nullable/defaulted columns on payments.payment_links and payments.message_outbox.

-- ---------- Website content (public schema) ----------
CREATE TABLE IF NOT EXISTS public.website_courses (
  course_id        integer PRIMARY KEY REFERENCES public.academic_courses(course_id) ON DELETE CASCADE,
  design_key       text UNIQUE,                 -- key the design pages use (hnd-se, c-aimastery...); defaults to the slug
  plan_type        text NOT NULL DEFAULT 'pg' CHECK (plan_type IN ('hnd','fnd','pg','cpd')),
  programme_code   text,                        -- Accounts programme (payments.programmes.code); blank = by plan type
  short_name       text,
  level            text,                        -- HND | Foundation | Top-Up | Postgraduate | Short course
  school           text,
  body_label       text,
  card_type        text,
  keywords         text,
  monthly_label    text,
  fee_label        text,
  fee_override     numeric(14,2),               -- charge this instead of the catalogue price
  cpd_addon_fee    numeric(14,2),               -- price before the 25% add-on discount
  auto_instalments boolean NOT NULL DEFAULT true,
  featured         boolean NOT NULL DEFAULT false,
  sort_order       integer NOT NULL DEFAULT 100,
  web_status       text NOT NULL DEFAULT 'visible' CHECK (web_status IN ('visible','hidden')),
  detail           jsonb,                       -- programme page fields that override the generated ones
  seo_title        text,
  seo_description  text,
  updated_at       timestamptz NOT NULL DEFAULT now(),
  updated_by       integer
);

CREATE TABLE IF NOT EXISTS public.website_faqs (
  id         bigserial PRIMARY KEY,
  audience   text NOT NULL DEFAULT 'student' CHECK (audience IN ('student','parent')),
  question   text NOT NULL,
  answer     text NOT NULL,
  sort_order integer NOT NULL DEFAULT 100,
  status     text NOT NULL DEFAULT 'draft' CHECK (status IN ('published','draft')),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.website_campaigns (
  id         bigserial PRIMARY KEY,
  key        text UNIQUE NOT NULL,
  tag        text NOT NULL DEFAULT '',
  text       text NOT NULL DEFAULT '',
  short      text NOT NULL DEFAULT '',
  perk       text NOT NULL DEFAULT '',
  sort_order integer NOT NULL DEFAULT 100,
  status     text NOT NULL DEFAULT 'draft' CHECK (status IN ('published','draft')),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.website_settings (
  key        text PRIMARY KEY,
  value      jsonb NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  updated_by integer
);

-- ---------- Free demo LMS (public schema) ----------
CREATE TABLE IF NOT EXISTS public.web_demo_users (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email         text UNIQUE NOT NULL,
  full_name     text NOT NULL,
  phone         text NOT NULL DEFAULT '',
  interest      text NOT NULL DEFAULT '',
  password_hash text NOT NULL,
  crm_lead_id   integer,
  created_at    timestamptz NOT NULL DEFAULT now(),
  expires_at    timestamptz NOT NULL,
  last_seen_at  timestamptz
);
CREATE TABLE IF NOT EXISTS public.web_demo_sessions (
  token_hash text PRIMARY KEY,
  user_id    uuid NOT NULL REFERENCES public.web_demo_users(id) ON DELETE CASCADE,
  expires_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS web_demo_sessions_user_idx ON public.web_demo_sessions(user_id);
CREATE TABLE IF NOT EXISTS public.web_demo_states (
  user_id    uuid PRIMARY KEY REFERENCES public.web_demo_users(id) ON DELETE CASCADE,
  state      jsonb NOT NULL DEFAULT '{}'::jsonb,
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS public.web_demo_events (
  id         bigserial PRIMARY KEY,
  user_id    uuid REFERENCES public.web_demo_users(id) ON DELETE CASCADE,
  who        text NOT NULL,
  label      text NOT NULL,
  points     integer NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS web_demo_events_created_idx ON public.web_demo_events(created_at DESC);

CREATE TABLE IF NOT EXISTS public.web_demo_classes (
  id            bigserial PRIMARY KEY,
  code          text NOT NULL DEFAULT '',
  title         text NOT NULL DEFAULT '',
  awarding_body text NOT NULL DEFAULT '',
  school        text NOT NULL DEFAULT '',
  programme     text NOT NULL DEFAULT '',
  intake        text NOT NULL DEFAULT '',
  study_mode    text NOT NULL DEFAULT 'Full Time',
  start_date    date,
  end_date      date,
  lecturers     text NOT NULL DEFAULT '',
  status        text NOT NULL DEFAULT 'Active' CHECK (status IN ('Planned','Active','Completed')),
  image         text NOT NULL DEFAULT '',
  description   text NOT NULL DEFAULT '',
  takeaways     text NOT NULL DEFAULT '',
  sort_order    integer NOT NULL DEFAULT 100,
  published     text NOT NULL DEFAULT 'draft' CHECK (published IN ('published','draft')),
  updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS public.web_demo_class_sections (
  id         bigserial PRIMARY KEY,
  class_id    bigint NOT NULL REFERENCES public.web_demo_classes(id) ON DELETE CASCADE,
  title       text NOT NULL,
  description text NOT NULL DEFAULT '',
  status      text NOT NULL DEFAULT 'active' CHECK (status IN ('active','draft')),
  position    integer NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS public.web_demo_class_items (
  id               bigserial PRIMARY KEY,
  section_id       bigint NOT NULL REFERENCES public.web_demo_class_sections(id) ON DELETE CASCADE,
  item_type        text NOT NULL DEFAULT 'video' CHECK (item_type IN ('video','pdf','pptx','text','link','assignment','quiz')),
  title            text NOT NULL,
  description      text NOT NULL DEFAULT '',
  resource_url     text NOT NULL DEFAULT '',
  text_content     text NOT NULL DEFAULT '',
  duration_minutes integer,
  is_required      boolean NOT NULL DEFAULT false,
  status           text NOT NULL DEFAULT 'published' CHECK (status IN ('published','draft')),
  position         integer NOT NULL DEFAULT 0
);

-- The API connects as the table owner, so RLS does not affect it; it only blocks other database roles.
ALTER TABLE public.website_courses ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.website_faqs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.website_campaigns ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.website_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.web_demo_users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.web_demo_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.web_demo_states ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.web_demo_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.web_demo_classes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.web_demo_class_sections ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.web_demo_class_items ENABLE ROW LEVEL SECURITY;

-- ---------- Website checkout (payments schema) ----------
-- An order stays outside the ledger until money is received (verified PayHere notify or approved bank slip).
CREATE TABLE IF NOT EXISTS payments.web_orders (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  reference          text UNIQUE NOT NULL,          -- IC-XXXXXX, shown to the student
  token              text UNIQUE NOT NULL,          -- secret, used in /pay/<token>
  status             text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','reserved','paid','expired','cancelled')),
  method             text NOT NULL CHECK (method IN ('payhere','bank_transfer')),
  full_name          text NOT NULL,
  email              text NOT NULL,
  phone              text NOT NULL,
  course_id          integer NOT NULL,
  course_title       text NOT NULL,
  plan_code          text NOT NULL CHECK (plan_code IN ('monthly','full','reserve')),
  plan_label         text NOT NULL DEFAULT '',
  plan_type          text NOT NULL,
  terms              jsonb NOT NULL,                -- server price snapshot (today, instalments, instalment amount)
  lines              jsonb NOT NULL,                -- [{course_id,title,programme_code,kind,amount,auto}]
  amount             numeric(14,2) NOT NULL CHECK (amount >= 0),
  crm_lead_id        integer,
  learner_id         uuid REFERENCES payments.learners(id),
  possible_duplicate boolean NOT NULL DEFAULT false,
  is_test            boolean NOT NULL DEFAULT false,
  extra              jsonb NOT NULL DEFAULT '{}'::jsonb,
  crm_synced_at      timestamptz,
  paid_at            timestamptz,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS web_orders_status_idx ON payments.web_orders(status, created_at DESC);
CREATE INDEX IF NOT EXISTS web_orders_email_idx ON payments.web_orders(lower(email));

CREATE TABLE IF NOT EXISTS payments.web_checkouts (
  order_id     text PRIMARY KEY,                    -- WEB-<reference>-<attempt>
  web_order_id uuid NOT NULL REFERENCES payments.web_orders(id),
  amount       numeric(14,2) NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS payments.web_enrolments (
  enrolment_id          uuid PRIMARY KEY REFERENCES payments.enrolments(id),
  web_order_id          uuid REFERENCES payments.web_orders(id),
  course_id             integer,
  plan_code             text NOT NULL,
  plan_label            text NOT NULL DEFAULT '',
  year                  integer NOT NULL DEFAULT 1,
  parent_enrolment_id   uuid REFERENCES payments.enrolments(id),
  activation_invoice_id uuid REFERENCES payments.invoices(id),
  activated_at          timestamptz,
  auto                  boolean NOT NULL DEFAULT true,
  instalment_count      integer NOT NULL DEFAULT 0,
  instalment_amount     numeric(14,2) NOT NULL DEFAULT 0,
  created_at            timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS payments.bank_slips (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  web_order_id uuid REFERENCES payments.web_orders(id),
  invoice_id   uuid REFERENCES payments.invoices(id),
  storage_key  text NOT NULL,
  file_name    text NOT NULL,
  mime         text NOT NULL,
  size_bytes   integer NOT NULL,
  amount       numeric(14,2) NOT NULL CHECK (amount > 0),
  paid_on      date,
  reference    text NOT NULL DEFAULT '',
  note         text NOT NULL DEFAULT '',
  status       text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
  reason       text,
  reviewed_by  text,
  reviewed_at  timestamptz,
  payment_id   uuid REFERENCES payments.payments(id),
  created_at   timestamptz NOT NULL DEFAULT now(),
  CHECK (web_order_id IS NOT NULL OR invoice_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS bank_slips_status_idx ON payments.bank_slips(status, created_at DESC);

ALTER TABLE payments.payment_links ADD COLUMN IF NOT EXISTS origin text NOT NULL DEFAULT 'accounts';
ALTER TABLE payments.message_outbox ADD COLUMN IF NOT EXISTS html_body text;
