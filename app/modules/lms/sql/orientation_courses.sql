ALTER TABLE lms_courses
ADD COLUMN IF NOT EXISTS is_orientation BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE lms_courses
ALTER COLUMN program_id DROP NOT NULL;

ALTER TABLE lms_courses DROP CONSTRAINT IF EXISTS ck_lms_courses_orientation;
ALTER TABLE lms_courses
ADD CONSTRAINT ck_lms_courses_orientation CHECK (
  (is_orientation AND program_id IS NULL AND catalogue_course_id IS NULL)
  OR (NOT is_orientation AND program_id IS NOT NULL)
);
