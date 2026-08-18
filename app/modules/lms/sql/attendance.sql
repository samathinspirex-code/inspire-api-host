-- Step 8: automatic Google Meet attendance and lecturer review.
ALTER TABLE lms_google_integration_settings
    ADD COLUMN IF NOT EXISTS attendance_threshold_percentage SMALLINT NOT NULL DEFAULT 50
        CHECK (attendance_threshold_percentage BETWEEN 1 AND 100);

CREATE TABLE IF NOT EXISTS lms_attendance_sessions (
    attendance_session_id BIGSERIAL PRIMARY KEY,
    meeting_id BIGINT NOT NULL UNIQUE
        REFERENCES lms_online_meetings(meeting_id) ON DELETE CASCADE,
    class_id INTEGER NOT NULL REFERENCES lms_classes(class_id) ON DELETE CASCADE,
    google_conference_record_name VARCHAR(255),
    actual_start_time TIMESTAMPTZ,
    actual_end_time TIMESTAMPTZ,
    threshold_percentage SMALLINT NOT NULL DEFAULT 50
        CHECK (threshold_percentage BETWEEN 1 AND 100),
    sync_status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (sync_status IN ('pending', 'synced', 'failed')),
    sync_error TEXT,
    unmatched_participants JSONB NOT NULL DEFAULT '[]'::jsonb,
    synced_by INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
    synced_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_lms_attendance_sessions_class
    ON lms_attendance_sessions(class_id);
CREATE INDEX IF NOT EXISTS idx_lms_attendance_sessions_status
    ON lms_attendance_sessions(sync_status);

CREATE TABLE IF NOT EXISTS lms_attendance_records (
    attendance_record_id BIGSERIAL PRIMARY KEY,
    attendance_session_id BIGINT NOT NULL
        REFERENCES lms_attendance_sessions(attendance_session_id) ON DELETE CASCADE,
    student_user_id INTEGER NOT NULL
        REFERENCES lms_student_profiles(user_id) ON DELETE CASCADE,
    status VARCHAR(10) NOT NULL CHECK (status IN ('present', 'absent')),
    attended_seconds INTEGER NOT NULL DEFAULT 0 CHECK (attended_seconds >= 0),
    attendance_percentage DOUBLE PRECISION NOT NULL DEFAULT 0
        CHECK (attendance_percentage BETWEEN 0 AND 100),
    first_join_time TIMESTAMPTZ,
    last_leave_time TIMESTAMPTZ,
    google_participant_name VARCHAR(255),
    source VARCHAR(20) NOT NULL DEFAULT 'google_meet'
        CHECK (source IN ('google_meet', 'manual_override')),
    overridden_by INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
    override_reason TEXT,
    overridden_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_lms_attendance_record_student
        UNIQUE (attendance_session_id, student_user_id)
);

CREATE INDEX IF NOT EXISTS idx_lms_attendance_records_student
    ON lms_attendance_records(student_user_id);
CREATE INDEX IF NOT EXISTS idx_lms_attendance_records_status
    ON lms_attendance_records(status);
