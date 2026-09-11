-- Remove course-level classification and make public entry/progression content CMS-managed.
ALTER TABLE academic_courses
  ADD COLUMN IF NOT EXISTS entry_requirements TEXT NOT NULL
  DEFAULT 'Contact admissions for entry requirements.';

ALTER TABLE academic_courses
  ADD COLUMN IF NOT EXISTS progression_route TEXT NOT NULL
  DEFAULT 'Contact admissions for progression options.';

UPDATE academic_courses SET level_id = NULL WHERE level_id IS NOT NULL;
UPDATE academic_programme_enrolments SET preferred_level_id = NULL WHERE preferred_level_id IS NOT NULL;
