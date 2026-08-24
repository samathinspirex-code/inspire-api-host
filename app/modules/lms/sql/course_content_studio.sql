ALTER TABLE lms_courses
    ADD COLUMN IF NOT EXISTS cover_image_url TEXT;

ALTER TABLE lms_courses
    ADD COLUMN IF NOT EXISTS takeaways TEXT;

CREATE TABLE IF NOT EXISTS lms_learning_items (
    learning_item_id BIGSERIAL PRIMARY KEY,
    module_id BIGINT NOT NULL REFERENCES lms_modules(module_id) ON DELETE CASCADE,
    item_type VARCHAR(30) NOT NULL CHECK (item_type IN ('video', 'pdf', 'text', 'link', 'assignment', 'quiz')),
    title VARCHAR(255) NOT NULL,
    description TEXT,
    resource_url TEXT,
    text_content TEXT,
    duration_minutes INTEGER CHECK (duration_minutes IS NULL OR duration_minutes > 0),
    position INTEGER NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'published')),
    is_required BOOLEAN NOT NULL DEFAULT TRUE,
    created_by BIGINT REFERENCES users(user_id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_lms_learning_items_module_position UNIQUE (module_id, position)
);

CREATE INDEX IF NOT EXISTS idx_lms_learning_items_module ON lms_learning_items(module_id);

CREATE TABLE IF NOT EXISTS lms_module_access (
    module_access_id BIGSERIAL PRIMARY KEY,
    module_id BIGINT NOT NULL REFERENCES lms_modules(module_id) ON DELETE CASCADE,
    scope_type VARCHAR(20) NOT NULL CHECK (scope_type IN ('course', 'class', 'student')),
    scope_id BIGINT NOT NULL,
    is_unlocked BOOLEAN NOT NULL DEFAULT TRUE,
    available_from TIMESTAMPTZ,
    created_by BIGINT REFERENCES users(user_id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_lms_module_access_scope UNIQUE (module_id, scope_type, scope_id)
);

CREATE INDEX IF NOT EXISTS idx_lms_module_access_lookup ON lms_module_access(module_id, scope_type, scope_id);

-- Preserve access to existing active modules when Course Studio is introduced.
INSERT INTO lms_module_access (module_id, scope_type, scope_id, is_unlocked)
SELECT module_id, 'course', course_id, TRUE
FROM lms_modules
WHERE status = 'active'
ON CONFLICT (module_id, scope_type, scope_id) DO NOTHING;

CREATE TABLE IF NOT EXISTS lms_course_discussions (
    discussion_id BIGSERIAL PRIMARY KEY,
    course_id BIGINT NOT NULL REFERENCES lms_courses(course_id) ON DELETE CASCADE,
    author_user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    message TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_lms_course_discussions_course_created
    ON lms_course_discussions(course_id, created_at);
