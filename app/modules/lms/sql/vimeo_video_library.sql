-- Dedicated Vimeo workspace references for LMS-managed course videos.
ALTER TABLE lms_courses
    ADD COLUMN IF NOT EXISTS vimeo_folder_uri TEXT;

ALTER TABLE lms_modules
    ADD COLUMN IF NOT EXISTS vimeo_folder_uri TEXT;

ALTER TABLE lms_learning_items
    ADD COLUMN IF NOT EXISTS thumbnail_url TEXT;
