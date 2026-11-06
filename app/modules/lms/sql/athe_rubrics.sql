CREATE TABLE IF NOT EXISTS lms_rubric_templates (
    template_id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    form_title VARCHAR(255),
    qualification VARCHAR(255),
    unit_code_title VARCHAR(255),
    default_assignment_title VARCHAR(255),
    source_file_name VARCHAR(255),
    is_archived BOOLEAN NOT NULL DEFAULT FALSE,
    created_by INTEGER NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
;
CREATE INDEX IF NOT EXISTS idx_lms_rubric_templates_active ON lms_rubric_templates (is_archived, updated_at DESC)
;
CREATE TABLE IF NOT EXISTS lms_rubric_template_criteria (
    criterion_id SERIAL PRIMARY KEY,
    template_id INTEGER NOT NULL REFERENCES lms_rubric_templates(template_id) ON DELETE CASCADE,
    lo_code VARCHAR(30) NOT NULL,
    lo_statement TEXT NOT NULL DEFAULT '',
    ac_code VARCHAR(30) NOT NULL,
    level VARCHAR(20) NOT NULL CHECK (level IN ('pass', 'merit', 'distinction')),
    statement TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 1
)
;
CREATE INDEX IF NOT EXISTS idx_lms_rubric_template_criteria ON lms_rubric_template_criteria (template_id, position)
;
CREATE TABLE IF NOT EXISTS lms_assignment_rubrics (
    assignment_id INTEGER PRIMARY KEY REFERENCES lms_coursework_assignments(assignment_id) ON DELETE CASCADE,
    template_id INTEGER REFERENCES lms_rubric_templates(template_id) ON DELETE SET NULL,
    template_name VARCHAR(255) NOT NULL,
    form_title VARCHAR(255),
    qualification VARCHAR(255),
    unit_code_title VARCHAR(255),
    assignment_title VARCHAR(255),
    criteria_snapshot JSONB NOT NULL DEFAULT '[]'::jsonb,
    first_assessor_user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    second_assessor_user_id INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
    issued_at DATE,
    created_by INTEGER NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_lms_assignment_rubrics_distinct_assessors
        CHECK (second_assessor_user_id IS NULL OR second_assessor_user_id <> first_assessor_user_id)
)
;
CREATE TABLE IF NOT EXISTS lms_rubric_assessments (
    assessment_id SERIAL PRIMARY KEY,
    assignment_id INTEGER NOT NULL REFERENCES lms_coursework_assignments(assignment_id) ON DELETE CASCADE,
    submission_id INTEGER NOT NULL REFERENCES lms_coursework_submissions(submission_id) ON DELETE CASCADE,
    assessor_role VARCHAR(10) NOT NULL CHECK (assessor_role IN ('first', 'second')),
    assessor_user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    criteria_results JSONB NOT NULL DEFAULT '[]'::jsonb,
    actions_needed TEXT,
    overall_comment TEXT,
    resubmission VARCHAR(100),
    assessment_date DATE,
    overall_grade VARCHAR(30),
    grade_summary JSONB,
    status VARCHAR(20) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'completed')),
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_lms_rubric_assessments_submission_role UNIQUE (submission_id, assessor_role)
)
;
CREATE INDEX IF NOT EXISTS idx_lms_rubric_assessments_assignment ON lms_rubric_assessments (assignment_id, assessor_role)
;
