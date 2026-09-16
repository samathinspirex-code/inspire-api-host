ALTER TABLE lms_courses
ADD COLUMN IF NOT EXISTS catalogue_course_id INT
REFERENCES academic_courses(course_id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_lms_courses_catalogue_course
ON lms_courses(catalogue_course_id);
