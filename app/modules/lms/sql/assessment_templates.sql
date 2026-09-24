CREATE TABLE IF NOT EXISTS lms_assessment_templates (
    template_id BIGSERIAL PRIMARY KEY,
    kind VARCHAR(20) NOT NULL,
    title VARCHAR(255) NOT NULL,
    instructions TEXT NOT NULL,
    assignment_type VARCHAR(20),
    submission_type VARCHAR(30),
    duration_minutes INTEGER,
    max_marks NUMERIC(8, 2) NOT NULL DEFAULT 0,
    created_by INTEGER NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_lms_assessment_templates_kind CHECK (kind IN ('assignment', 'practice_test'))
);

CREATE INDEX IF NOT EXISTS idx_lms_assessment_templates_kind
    ON lms_assessment_templates (kind, updated_at DESC);

CREATE TABLE IF NOT EXISTS lms_assessment_template_questions (
    question_id BIGSERIAL PRIMARY KEY,
    template_id BIGINT NOT NULL REFERENCES lms_assessment_templates(template_id) ON DELETE CASCADE,
    question_type VARCHAR(20) NOT NULL,
    prompt TEXT NOT NULL,
    marks NUMERIC(8, 2) NOT NULL,
    position INTEGER NOT NULL DEFAULT 1,
    options JSONB,
    correct_option_index INTEGER,
    correct_option_indices JSONB,
    accepted_answers JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_lms_assessment_template_questions
    ON lms_assessment_template_questions (template_id, position);
