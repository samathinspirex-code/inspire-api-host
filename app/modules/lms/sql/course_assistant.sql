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
