CREATE TABLE IF NOT EXISTS lms_course_assistant_settings (
    course_id BIGINT PRIMARY KEY REFERENCES lms_courses(course_id) ON DELETE CASCADE,
    is_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    assistant_name VARCHAR(80) NOT NULL DEFAULT 'Course Assistant',
    welcome_message TEXT NOT NULL DEFAULT 'Hi! What would you like to understand about this course?',
    fallback_message TEXT NOT NULL DEFAULT 'I couldn''t find that in the approved course resources yet.',
    attention_animation BOOLEAN NOT NULL DEFAULT TRUE,
    created_by BIGINT REFERENCES users(user_id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS lms_course_assistant_system_settings (
    settings_id INTEGER PRIMARY KEY DEFAULT 1 CHECK (settings_id = 1),
    automation_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    auto_generate_questions BOOLEAN NOT NULL DEFAULT TRUE,
    questions_per_video INTEGER NOT NULL DEFAULT 20 CHECK (questions_per_video BETWEEN 4 AND 50),
    updated_by BIGINT REFERENCES users(user_id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO lms_course_assistant_system_settings (settings_id)
VALUES (1)
ON CONFLICT (settings_id) DO NOTHING;

-- Courses that were released before assistant automation was introduced should
-- become available without asking the lecturer to release the same week again.
INSERT INTO lms_course_assistant_settings (
    course_id,
    is_enabled,
    assistant_name,
    welcome_message,
    fallback_message,
    attention_animation,
    created_by
)
SELECT DISTINCT
    module.course_id,
    TRUE,
    'Lecture Assistant',
    'Hi! What would you like to understand about this course?',
    'I couldn''t find that in the approved course resources yet.',
    TRUE,
    access.created_by
FROM lms_module_access AS access
JOIN lms_modules AS module ON module.module_id = access.module_id
WHERE access.is_unlocked = TRUE
ON CONFLICT (course_id) DO NOTHING;

CREATE TABLE IF NOT EXISTS lms_course_knowledge_sources (
    knowledge_source_id BIGSERIAL PRIMARY KEY,
    course_id BIGINT NOT NULL REFERENCES lms_courses(course_id) ON DELETE CASCADE,
    learning_item_id BIGINT REFERENCES lms_learning_items(learning_item_id) ON DELETE CASCADE,
    source_type VARCHAR(30) NOT NULL CHECK (source_type IN ('video_transcript', 'pdf', 'text_lesson', 'lecturer_note', 'faq')),
    title VARCHAR(255) NOT NULL,
    content TEXT NOT NULL,
    source_url TEXT,
    page_number INTEGER CHECK (page_number IS NULL OR page_number > 0),
    start_seconds INTEGER CHECK (start_seconds IS NULL OR start_seconds >= 0),
    end_seconds INTEGER CHECK (end_seconds IS NULL OR end_seconds >= 0),
    is_approved BOOLEAN NOT NULL DEFAULT TRUE,
    created_by BIGINT REFERENCES users(user_id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_lms_course_knowledge_sources_course ON lms_course_knowledge_sources(course_id);
CREATE INDEX IF NOT EXISTS idx_lms_course_knowledge_sources_item ON lms_course_knowledge_sources(learning_item_id);

ALTER TABLE lms_course_knowledge_sources
    ADD COLUMN IF NOT EXISTS sync_key VARCHAR(255),
    ADD COLUMN IF NOT EXISTS ingestion_status VARCHAR(30) NOT NULL DEFAULT 'manual',
    ADD COLUMN IF NOT EXISTS indexed_at TIMESTAMPTZ;

CREATE UNIQUE INDEX IF NOT EXISTS uq_lms_course_knowledge_sources_sync_key
    ON lms_course_knowledge_sources(course_id, sync_key)
    WHERE sync_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS lms_course_knowledge_chunks (
    knowledge_chunk_id BIGSERIAL PRIMARY KEY,
    knowledge_source_id BIGINT NOT NULL REFERENCES lms_course_knowledge_sources(knowledge_source_id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    position INTEGER NOT NULL,
    page_number INTEGER CHECK (page_number IS NULL OR page_number > 0),
    start_seconds INTEGER CHECK (start_seconds IS NULL OR start_seconds >= 0),
    end_seconds INTEGER CHECK (end_seconds IS NULL OR end_seconds >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_lms_course_knowledge_chunks_position UNIQUE (knowledge_source_id, position)
);

CREATE INDEX IF NOT EXISTS idx_lms_course_knowledge_chunks_source
    ON lms_course_knowledge_chunks(knowledge_source_id);

-- Existing manually entered passages remain searchable after upgrading.
INSERT INTO lms_course_knowledge_chunks (
    knowledge_source_id, content, position, page_number, start_seconds, end_seconds
)
SELECT knowledge_source_id, content, 1, page_number, start_seconds, end_seconds
FROM lms_course_knowledge_sources
WHERE length(trim(content)) > 0
ON CONFLICT (knowledge_source_id, position) DO NOTHING;

CREATE TABLE IF NOT EXISTS lms_lecture_questions (
    question_id BIGSERIAL PRIMARY KEY,
    course_id BIGINT NOT NULL REFERENCES lms_courses(course_id) ON DELETE CASCADE,
    learning_item_id BIGINT NOT NULL REFERENCES lms_learning_items(learning_item_id) ON DELETE CASCADE,
    question TEXT NOT NULL,
    option_a TEXT NOT NULL,
    option_b TEXT NOT NULL,
    option_c TEXT NOT NULL,
    option_d TEXT NOT NULL,
    correct_option VARCHAR(1) NOT NULL CHECK (correct_option IN ('A', 'B', 'C', 'D')),
    explanation TEXT NOT NULL,
    difficulty VARCHAR(10) NOT NULL DEFAULT 'medium' CHECK (difficulty IN ('easy', 'medium', 'hard')),
    topic VARCHAR(120) NOT NULL DEFAULT 'General',
    source_locator VARCHAR(120),
    status VARCHAR(20) NOT NULL DEFAULT 'generated' CHECK (status IN ('generated', 'approved', 'rejected')),
    generated_by_ai BOOLEAN NOT NULL DEFAULT TRUE,
    created_by BIGINT REFERENCES users(user_id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_lms_lecture_questions_item_status
    ON lms_lecture_questions(learning_item_id, status);
CREATE INDEX IF NOT EXISTS idx_lms_lecture_questions_course
    ON lms_lecture_questions(course_id);

CREATE TABLE IF NOT EXISTS lms_lecture_quiz_attempts (
    attempt_id BIGSERIAL PRIMARY KEY,
    learning_item_id BIGINT NOT NULL REFERENCES lms_learning_items(learning_item_id) ON DELETE CASCADE,
    student_user_id BIGINT NOT NULL REFERENCES lms_student_profiles(user_id) ON DELETE CASCADE,
    score INTEGER,
    total_questions INTEGER NOT NULL CHECK (total_questions BETWEEN 1 AND 30),
    submitted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_lms_lecture_quiz_attempts_student_item
    ON lms_lecture_quiz_attempts(student_user_id, learning_item_id);

CREATE TABLE IF NOT EXISTS lms_lecture_quiz_attempt_questions (
    attempt_question_id BIGSERIAL PRIMARY KEY,
    attempt_id BIGINT NOT NULL REFERENCES lms_lecture_quiz_attempts(attempt_id) ON DELETE CASCADE,
    question_id BIGINT NOT NULL REFERENCES lms_lecture_questions(question_id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    selected_option VARCHAR(1) CHECK (selected_option IS NULL OR selected_option IN ('A', 'B', 'C', 'D')),
    is_correct BOOLEAN,
    CONSTRAINT uq_lms_attempt_question UNIQUE (attempt_id, question_id),
    CONSTRAINT uq_lms_attempt_question_position UNIQUE (attempt_id, position)
);
