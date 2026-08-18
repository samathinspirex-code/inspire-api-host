CREATE TABLE IF NOT EXISTS lms_learning_progress (
    progress_id BIGSERIAL PRIMARY KEY,
    learning_item_id BIGINT NOT NULL REFERENCES lms_learning_items(learning_item_id) ON DELETE CASCADE,
    student_user_id BIGINT NOT NULL REFERENCES lms_student_profiles(user_id) ON DELETE CASCADE,
    watched_seconds INTEGER NOT NULL DEFAULT 0 CHECK (watched_seconds >= 0),
    duration_seconds INTEGER CHECK (duration_seconds IS NULL OR duration_seconds > 0),
    last_position_seconds INTEGER NOT NULL DEFAULT 0 CHECK (last_position_seconds >= 0),
    completion_percent DOUBLE PRECISION NOT NULL DEFAULT 0 CHECK (completion_percent >= 0 AND completion_percent <= 100),
    is_completed BOOLEAN NOT NULL DEFAULT FALSE,
    completed_at TIMESTAMPTZ,
    last_activity_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_lms_learning_progress_item_student UNIQUE (learning_item_id, student_user_id)
);

CREATE INDEX IF NOT EXISTS idx_lms_learning_progress_student
    ON lms_learning_progress(student_user_id, last_activity_at DESC);

CREATE INDEX IF NOT EXISTS idx_lms_learning_progress_item
    ON lms_learning_progress(learning_item_id);
