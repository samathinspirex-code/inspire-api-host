-- Step 2: ordered modules inside LMS courses.
CREATE TABLE IF NOT EXISTS lms_modules (
    module_id   SERIAL PRIMARY KEY,
    course_id   INT NOT NULL REFERENCES lms_courses(course_id) ON DELETE CASCADE,
    title       VARCHAR(255) NOT NULL,
    description TEXT,
    position    INT NOT NULL CHECK (position > 0),
    status      VARCHAR(20) NOT NULL DEFAULT 'draft'
                CHECK (status IN ('draft', 'active')),
    created_by  INT REFERENCES users(user_id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_lms_modules_course_position UNIQUE (course_id, position)
);

CREATE INDEX IF NOT EXISTS idx_lms_modules_course_id ON lms_modules(course_id);
