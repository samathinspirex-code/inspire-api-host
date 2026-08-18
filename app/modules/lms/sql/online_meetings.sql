-- Step 7: Google Meet-backed online class schedule.
CREATE TABLE IF NOT EXISTS lms_online_meetings (
    meeting_id BIGSERIAL PRIMARY KEY,
    class_id INTEGER NOT NULL REFERENCES lms_classes(class_id) ON DELETE CASCADE,
    lecturer_user_id INTEGER NOT NULL REFERENCES lms_lecturer_profiles(user_id) ON DELETE CASCADE,
    title VARCHAR(255) NOT NULL,
    description TEXT,
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ NOT NULL,
    timezone VARCHAR(100) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'scheduled'
        CHECK (status IN ('scheduled', 'cancelled', 'completed')),
    google_space_name VARCHAR(255) NOT NULL UNIQUE,
    google_meeting_uri TEXT NOT NULL,
    google_meeting_code VARCHAR(128) NOT NULL,
    google_calendar_event_id VARCHAR(255),
    google_calendar_event_uri TEXT,
    calendar_sync_status VARCHAR(20) NOT NULL
        CHECK (calendar_sync_status IN ('synced', 'disabled', 'failed')),
    calendar_sync_error TEXT,
    students_notified BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (end_time > start_time)
);

CREATE INDEX IF NOT EXISTS idx_lms_online_meetings_class ON lms_online_meetings(class_id);
CREATE INDEX IF NOT EXISTS idx_lms_online_meetings_lecturer ON lms_online_meetings(lecturer_user_id);
CREATE INDEX IF NOT EXISTS idx_lms_online_meetings_start ON lms_online_meetings(start_time);
