CREATE TABLE IF NOT EXISTS lms_coursework_assignments (
    assignment_id BIGSERIAL PRIMARY KEY,
    course_id BIGINT NOT NULL REFERENCES lms_courses(course_id) ON DELETE CASCADE,
    target_type VARCHAR(20) NOT NULL DEFAULT 'course' CHECK (target_type IN ('course', 'class')),
    target_id BIGINT NOT NULL,
    title VARCHAR(255) NOT NULL,
    instructions TEXT NOT NULL,
    assignment_type VARCHAR(20) NOT NULL DEFAULT 'regular' CHECK (assignment_type IN ('regular', 'timed')),
    available_from TIMESTAMPTZ,
    due_at TIMESTAMPTZ,
    duration_minutes INTEGER CHECK (duration_minutes IS NULL OR duration_minutes BETWEEN 1 AND 1440),
    max_marks NUMERIC(8,2) NOT NULL DEFAULT 100 CHECK (max_marks > 0),
    allow_late BOOLEAN NOT NULL DEFAULT FALSE,
    grades_released BOOLEAN NOT NULL DEFAULT FALSE,
    status VARCHAR(20) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'published', 'closed')),
    created_by BIGINT NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (assignment_type <> 'timed' OR duration_minutes IS NOT NULL),
    CHECK (assignment_type <> 'timed' OR allow_late = FALSE),
    CHECK (available_from IS NULL OR due_at IS NULL OR due_at > available_from)
);

ALTER TABLE lms_coursework_assignments
    ADD COLUMN IF NOT EXISTS grades_released BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_lms_coursework_course_status
    ON lms_coursework_assignments(course_id, status);
CREATE INDEX IF NOT EXISTS idx_lms_coursework_target
    ON lms_coursework_assignments(target_type, target_id);

CREATE TABLE IF NOT EXISTS lms_coursework_submissions (
    submission_id BIGSERIAL PRIMARY KEY,
    assignment_id BIGINT NOT NULL REFERENCES lms_coursework_assignments(assignment_id) ON DELETE CASCADE,
    student_user_id BIGINT NOT NULL REFERENCES lms_student_profiles(user_id) ON DELETE CASCADE,
    status VARCHAR(20) NOT NULL DEFAULT 'in_progress' CHECK (status IN ('in_progress', 'submitted', 'expired', 'reviewed', 'returned')),
    started_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ,
    answer_text TEXT,
    attachment_asset_id BIGINT REFERENCES media_assets(media_asset_id) ON DELETE SET NULL,
    submitted_at TIMESTAMPTZ,
    marks_awarded NUMERIC(8,2),
    feedback TEXT,
    marked_by BIGINT REFERENCES users(user_id) ON DELETE SET NULL,
    marked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_lms_coursework_submission_student UNIQUE (assignment_id, student_user_id)
);

CREATE INDEX IF NOT EXISTS idx_lms_coursework_submission_assignment
    ON lms_coursework_submissions(assignment_id, status);
CREATE INDEX IF NOT EXISTS idx_lms_coursework_submission_student
    ON lms_coursework_submissions(student_user_id, updated_at DESC);
