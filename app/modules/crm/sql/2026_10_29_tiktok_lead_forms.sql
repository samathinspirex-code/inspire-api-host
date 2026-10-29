-- The original TikTok source did not distinguish chat from lead forms.
-- The CRM owner classified all existing generic TikTok leads as lead forms.
UPDATE crm_leads
SET source = 'tiktok_lead_forms'
WHERE source IN ('tiktok', 'tik_tok');
