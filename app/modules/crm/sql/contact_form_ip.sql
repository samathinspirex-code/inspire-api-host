-- Keep the website-reported visitor address and the API's independent socket
-- address separately. Both are restricted to the authenticated CRM lead view.
ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS contact_visitor_ip VARCHAR(45);
ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS contact_peer_ip VARCHAR(45);

CREATE INDEX IF NOT EXISTS idx_crm_contact_visitor_ip
  ON crm_leads (contact_visitor_ip)
  WHERE source = 'website_contact' AND contact_visitor_ip IS NOT NULL;
