-- Additive CRM rebuild. Run before deploying the new API.
ALTER TABLE crm_leads ALTER COLUMN stage SET DEFAULT 'new_lead';
ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS status_reason VARCHAR(150);
ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS status_remarks TEXT;
ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS affordability_reason VARCHAR(150);
ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS expected_intake VARCHAR(100);
ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS expected_month VARCHAR(20);
ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS delay_reason VARCHAR(150);
ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS campaign VARCHAR(255);
ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS assigned_at TIMESTAMP;
ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS academic_course_id INTEGER;
ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS programme_fee NUMERIC(12,2);
ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS registration_fee NUMERIC(12,2);
ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS programme_duration VARCHAR(100);
ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS intake VARCHAR(100);
ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS payment_plan TEXT;
ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS last_contacted_at TIMESTAMP;
ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS last_activity_at TIMESTAMP;
ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS followup_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS enrolled_at TIMESTAMP;
ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS student_user_id INTEGER;
ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS student_id VARCHAR(100);
ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS payment_status VARCHAR(30);
ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS amount_paid NUMERIC(12,2);
ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS payment_date TIMESTAMP;
ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS enrollment_email_status VARCHAR(30);
ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS enrollment_email_error TEXT;
ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS enrollment_email_sent_at TIMESTAMP;
CREATE INDEX IF NOT EXISTS ix_crm_leads_course ON crm_leads(academic_course_id);
CREATE INDEX IF NOT EXISTS ix_crm_leads_contacted ON crm_leads(last_contacted_at);
CREATE INDEX IF NOT EXISTS ix_crm_leads_followup ON crm_leads(followup_date);
-- Permit new website admission submissions during the deployment window while
-- historical admission rows still carry the old value until the CRM cutover.
ALTER TABLE crm_admission_leads DROP CONSTRAINT IF EXISTS crm_admission_leads_status_check;
ALTER TABLE crm_admission_leads ALTER COLUMN status SET DEFAULT 'new_lead';
ALTER TABLE crm_admission_leads ADD CONSTRAINT crm_admission_leads_status_check
  CHECK (status IN ('new_lead', 'uncontactable', 'contactable', 'future_prospect',
                   'not_interested', 'lost_to_competitor', 'cant_afford',
                   'new_inquiry', 'contacted', 'counselling',
                   'application_started', 'documents_pending', 'application_submitted',
                   'offer_sent', 'enrolled', 'lost', 'deferred'));
