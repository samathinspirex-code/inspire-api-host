CREATE TABLE IF NOT EXISTS crm_admission_leads (
    lead_id SERIAL PRIMARY KEY,
    submission_id UUID NOT NULL UNIQUE,
    full_name VARCHAR(255) NOT NULL,
    email VARCHAR(320) NOT NULL,
    phone VARCHAR(50) NOT NULL,
    highest_qualification VARCHAR(120) NOT NULL,
    programme_id INT NOT NULL REFERENCES academic_programmes(programme_id) ON DELETE RESTRICT,
    preferred_course_id INT REFERENCES academic_courses(course_id) ON DELETE SET NULL,
    result_document_key TEXT,
    result_document_name VARCHAR(255),
    result_document_content_type VARCHAR(100),
    status VARCHAR(30) NOT NULL DEFAULT 'new_inquiry'
      CHECK (status IN ('new_inquiry','contacted','counselling','application_started','documents_pending','application_submitted','offer_sent','enrolled','lost','deferred')),
    source VARCHAR(50) NOT NULL DEFAULT 'website_admissions',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_crm_admission_leads_status_created
    ON crm_admission_leads(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_crm_admission_leads_email
    ON crm_admission_leads(lower(email));
