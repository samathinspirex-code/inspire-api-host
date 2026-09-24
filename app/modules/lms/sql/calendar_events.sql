CREATE TABLE IF NOT EXISTS lms_calendar_events (
    event_id SERIAL PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    description TEXT,
    location VARCHAR(255),
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ NOT NULL,
    audience_type VARCHAR(20) NOT NULL,
    program_id INTEGER REFERENCES programs(program_id) ON DELETE CASCADE,
    class_id INTEGER REFERENCES lms_classes(class_id) ON DELETE CASCADE,
    status VARCHAR(20) NOT NULL DEFAULT 'scheduled',
    created_by INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_lms_calendar_events_audience_type CHECK (audience_type IN ('university', 'programme', 'class')),
    CONSTRAINT ck_lms_calendar_events_status CHECK (status IN ('scheduled', 'cancelled')),
    CONSTRAINT ck_lms_calendar_events_audience CHECK (
        (audience_type = 'university' AND program_id IS NULL AND class_id IS NULL)
        OR (audience_type = 'programme' AND program_id IS NOT NULL AND class_id IS NULL)
        OR (audience_type = 'class' AND class_id IS NOT NULL AND program_id IS NULL)
    ),
    CONSTRAINT ck_lms_calendar_events_time CHECK (end_time > start_time)
);

CREATE INDEX IF NOT EXISTS idx_lms_calendar_events_start ON lms_calendar_events (start_time);

CREATE INDEX IF NOT EXISTS idx_lms_calendar_events_audience ON lms_calendar_events (audience_type, program_id, class_id);
