-- Optional course subtitle; existing courses remain unchanged.
ALTER TABLE academic_courses
  ADD COLUMN IF NOT EXISTS subtitle_enabled BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS subtitle VARCHAR(255);

ALTER TABLE programs
  ADD COLUMN IF NOT EXISTS subtitle_enabled BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS subtitle VARCHAR(255);
