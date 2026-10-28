-- Rename the CRM pipeline stage without changing lead creation or activity dates.
-- Run all statements in one transaction.

UPDATE crm_leads
SET stage = 'contacted'
WHERE stage = 'contactable';

ALTER TABLE crm_admission_leads
DROP CONSTRAINT IF EXISTS crm_admission_leads_status_check;

UPDATE crm_admission_leads
SET status = 'contacted'
WHERE status = 'contactable';

ALTER TABLE crm_admission_leads
ADD CONSTRAINT crm_admission_leads_status_check
CHECK (status IN (
  'new_lead', 'uncontactable', 'contacted', 'future_prospect',
  'not_interested', 'lost_to_competitor', 'cant_afford',
  'new_inquiry', 'counselling', 'application_started',
  'documents_pending', 'application_submitted', 'offer_sent',
  'enrolled', 'lost', 'deferred'
));
