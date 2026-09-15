-- Zoom provider, shared host pool, attendance jobs, and Vimeo recording links.
ALTER TABLE lms_online_meetings DROP CONSTRAINT IF EXISTS ck_lms_online_meetings_provider;
ALTER TABLE lms_online_meetings ADD COLUMN IF NOT EXISTS provider VARCHAR(20) NOT NULL DEFAULT 'google';
ALTER TABLE lms_online_meetings ADD CONSTRAINT ck_lms_online_meetings_provider CHECK (provider IN ('google','zoom'));
ALTER TABLE lms_online_meetings ADD COLUMN IF NOT EXISTS join_uri TEXT NOT NULL DEFAULT '';
ALTER TABLE lms_online_meetings ADD COLUMN IF NOT EXISTS provider_meeting_id VARCHAR(255);
ALTER TABLE lms_online_meetings ADD COLUMN IF NOT EXISTS provider_meeting_uuid VARCHAR(255);
ALTER TABLE lms_online_meetings ADD COLUMN IF NOT EXISTS zoom_host_connection_id BIGINT;
ALTER TABLE lms_online_meetings ADD COLUMN IF NOT EXISTS zoom_passcode_encrypted TEXT;
ALTER TABLE lms_online_meetings ADD COLUMN IF NOT EXISTS processing_status VARCHAR(30) NOT NULL DEFAULT 'not_applicable';
ALTER TABLE lms_online_meetings ADD COLUMN IF NOT EXISTS processing_error TEXT;
ALTER TABLE lms_online_meetings ALTER COLUMN google_space_name DROP NOT NULL;
ALTER TABLE lms_online_meetings ALTER COLUMN google_meeting_uri DROP NOT NULL;
ALTER TABLE lms_online_meetings ALTER COLUMN google_meeting_code DROP NOT NULL;
UPDATE lms_online_meetings SET join_uri=google_meeting_uri WHERE join_uri='' AND google_meeting_uri IS NOT NULL;

CREATE TABLE IF NOT EXISTS lms_zoom_settings (
  settings_id SMALLINT PRIMARY KEY DEFAULT 1 CHECK (settings_id=1),
  enabled BOOLEAN NOT NULL DEFAULT FALSE,
  active_provider VARCHAR(20) NOT NULL DEFAULT 'google' CHECK (active_provider IN ('google','zoom')),
  attendance_sync_enabled BOOLEAN NOT NULL DEFAULT TRUE,
  attendance_threshold_percentage SMALLINT NOT NULL DEFAULT 50 CHECK (attendance_threshold_percentage BETWEEN 1 AND 100),
  automatic_recording BOOLEAN NOT NULL DEFAULT TRUE,
  updated_by INT REFERENCES users(user_id) ON DELETE SET NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO lms_zoom_settings(settings_id) VALUES(1) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS lms_zoom_oauth_states (
  state_hash VARCHAR(64) PRIMARY KEY,
  requested_by INT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  expires_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS lms_zoom_host_connections (
  connection_id BIGSERIAL PRIMARY KEY,
  zoom_user_id VARCHAR(255) NOT NULL UNIQUE,
  zoom_account_id VARCHAR(255),
  email VARCHAR(255) NOT NULL,
  encrypted_access_token TEXT NOT NULL,
  encrypted_refresh_token TEXT NOT NULL,
  token_expires_at TIMESTAMPTZ NOT NULL,
  capacity SMALLINT NOT NULL DEFAULT 1 CHECK (capacity IN (1,2)),
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  connected_by INT REFERENCES users(user_id) ON DELETE SET NULL,
  connected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE lms_online_meetings DROP CONSTRAINT IF EXISTS fk_lms_meeting_zoom_host;
ALTER TABLE lms_online_meetings ADD CONSTRAINT fk_lms_meeting_zoom_host FOREIGN KEY (zoom_host_connection_id) REFERENCES lms_zoom_host_connections(connection_id) ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS lms_zoom_registrations (
  meeting_id BIGINT NOT NULL REFERENCES lms_online_meetings(meeting_id) ON DELETE CASCADE,
  student_user_id INT NOT NULL REFERENCES lms_student_profiles(user_id) ON DELETE CASCADE,
  zoom_registrant_id VARCHAR(255),
  encrypted_join_token TEXT,
  join_url TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY(meeting_id,student_user_id)
);

CREATE TABLE IF NOT EXISTS lms_zoom_jobs (
  job_id BIGSERIAL PRIMARY KEY,
  event_key VARCHAR(255) NOT NULL UNIQUE,
  meeting_id BIGINT REFERENCES lms_online_meetings(meeting_id) ON DELETE CASCADE,
  job_type VARCHAR(30) NOT NULL CHECK (job_type IN ('attendance','recording')),
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  status VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','processing','completed','failed')),
  attempts INT NOT NULL DEFAULT 0,
  available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_lms_zoom_jobs_claim ON lms_zoom_jobs(status,available_at);

CREATE TABLE IF NOT EXISTS lms_zoom_recordings (
  recording_id BIGSERIAL PRIMARY KEY,
  meeting_id BIGINT NOT NULL REFERENCES lms_online_meetings(meeting_id) ON DELETE CASCADE,
  zoom_recording_file_id VARCHAR(255) NOT NULL UNIQUE,
  recording_type VARCHAR(100),
  part_number INT NOT NULL DEFAULT 1,
  status VARCHAR(30) NOT NULL DEFAULT 'pending',
  vimeo_video_uri TEXT,
  learning_item_id INT REFERENCES lms_learning_items(learning_item_id) ON DELETE SET NULL,
  error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE lms_attendance_sessions ADD COLUMN IF NOT EXISTS provider_reference VARCHAR(255);
ALTER TABLE lms_attendance_records DROP CONSTRAINT IF EXISTS ck_lms_attendance_source;
ALTER TABLE lms_attendance_records DROP CONSTRAINT IF EXISTS lms_attendance_records_source_check;
ALTER TABLE lms_attendance_records ADD CONSTRAINT ck_lms_attendance_source CHECK (source IN ('google_meet','zoom','manual_override'));
ALTER TABLE lms_learning_items ADD COLUMN IF NOT EXISTS origin VARCHAR(30) NOT NULL DEFAULT 'manual';
ALTER TABLE lms_learning_items ADD COLUMN IF NOT EXISTS ai_eligible BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE lms_learning_items ADD COLUMN IF NOT EXISTS quiz_eligible BOOLEAN NOT NULL DEFAULT TRUE;
