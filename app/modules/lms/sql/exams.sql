CREATE TABLE IF NOT EXISTS lms_exams (
    exam_id BIGSERIAL PRIMARY KEY,
    assignment_id BIGINT NOT NULL UNIQUE REFERENCES lms_coursework_assignments(assignment_id) ON DELETE CASCADE,
    course_id BIGINT NOT NULL REFERENCES lms_courses(course_id) ON DELETE CASCADE,
    target_type VARCHAR(20) NOT NULL CHECK (target_type IN ('course', 'class')),
    target_id BIGINT NOT NULL,
    title VARCHAR(255) NOT NULL,
    instructions TEXT NOT NULL,
    available_from TIMESTAMPTZ,
    due_at TIMESTAMPTZ,
    duration_minutes INTEGER NOT NULL CHECK (duration_minutes BETWEEN 1 AND 1440),
    randomize_questions BOOLEAN NOT NULL DEFAULT TRUE,
    randomize_options BOOLEAN NOT NULL DEFAULT TRUE,
    grades_released BOOLEAN NOT NULL DEFAULT FALSE,
    status VARCHAR(20) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'published', 'closed')),
    created_by BIGINT NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (available_from IS NULL OR due_at IS NULL OR due_at > available_from)
);

CREATE INDEX IF NOT EXISTS idx_lms_exams_course_status ON lms_exams(course_id, status);

CREATE TABLE IF NOT EXISTS lms_exam_questions (
    question_id BIGSERIAL PRIMARY KEY,
    exam_id BIGINT NOT NULL REFERENCES lms_exams(exam_id) ON DELETE CASCADE,
    question_type VARCHAR(20) NOT NULL CHECK (question_type IN ('mcq', 'short_answer', 'essay')),
    prompt TEXT NOT NULL,
    marks NUMERIC(8,2) NOT NULL CHECK (marks > 0),
    position INTEGER NOT NULL DEFAULT 1,
    options JSONB,
    correct_option_index INTEGER,
    accepted_answers JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (question_type <> 'mcq' OR (jsonb_array_length(options) >= 2 AND correct_option_index IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS idx_lms_exam_questions_exam ON lms_exam_questions(exam_id, position);

CREATE TABLE IF NOT EXISTS lms_exam_attempts (
    attempt_id BIGSERIAL PRIMARY KEY,
    exam_id BIGINT NOT NULL REFERENCES lms_exams(exam_id) ON DELETE CASCADE,
    student_user_id BIGINT NOT NULL REFERENCES lms_student_profiles(user_id) ON DELETE CASCADE,
    status VARCHAR(20) NOT NULL DEFAULT 'in_progress' CHECK (status IN ('in_progress', 'submitted', 'expired', 'reviewed')),
    started_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    submitted_at TIMESTAMPTZ,
    question_order JSONB NOT NULL,
    option_orders JSONB NOT NULL DEFAULT '{}'::jsonb,
    auto_marks NUMERIC(8,2) NOT NULL DEFAULT 0,
    manual_marks NUMERIC(8,2),
    total_marks NUMERIC(8,2),
    feedback TEXT,
    marked_by BIGINT REFERENCES users(user_id) ON DELETE SET NULL,
    marked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_lms_exam_attempt_student UNIQUE (exam_id, student_user_id)
);

CREATE INDEX IF NOT EXISTS idx_lms_exam_attempts_exam ON lms_exam_attempts(exam_id, status);

CREATE TABLE IF NOT EXISTS lms_exam_answers (
    answer_id BIGSERIAL PRIMARY KEY,
    attempt_id BIGINT NOT NULL REFERENCES lms_exam_attempts(attempt_id) ON DELETE CASCADE,
    question_id BIGINT NOT NULL REFERENCES lms_exam_questions(question_id) ON DELETE CASCADE,
    answer_text TEXT,
    selected_option_index INTEGER,
    is_correct BOOLEAN,
    auto_marks NUMERIC(8,2) NOT NULL DEFAULT 0,
    manual_marks NUMERIC(8,2),
    feedback TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_lms_exam_answer_question UNIQUE (attempt_id, question_id)
);
