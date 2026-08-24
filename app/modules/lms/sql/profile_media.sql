ALTER TABLE lms_student_profiles
    ADD COLUMN IF NOT EXISTS profile_image_url TEXT;

ALTER TABLE lms_lecturer_profiles
    ADD COLUMN IF NOT EXISTS profile_image_url TEXT;
