-- Phase 1: courses attached to the existing CMS programme catalogue.
CREATE TABLE IF NOT EXISTS lms_courses (
    course_id   SERIAL PRIMARY KEY,
    program_id  INT NOT NULL REFERENCES programs(program_id) ON DELETE RESTRICT,
    code        VARCHAR(100) NOT NULL UNIQUE,
    title       VARCHAR(255) NOT NULL,
    description TEXT,
    cover_image_url TEXT,
    status      VARCHAR(20) NOT NULL DEFAULT 'draft'
                CHECK (status IN ('draft', 'active', 'archived')),
    created_by  INT REFERENCES users(user_id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_lms_courses_program_id ON lms_courses(program_id);
CREATE INDEX IF NOT EXISTS idx_lms_courses_status ON lms_courses(status);
