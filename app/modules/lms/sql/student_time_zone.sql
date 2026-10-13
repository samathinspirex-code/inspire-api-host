ALTER TABLE lms_student_profiles
    ADD COLUMN IF NOT EXISTS time_zone VARCHAR(100);

UPDATE lms_student_profiles SET country = 'Sri Lanka' WHERE country IS NULL OR btrim(country) = '';
UPDATE lms_student_profiles SET time_zone = 'Asia/Colombo'
WHERE (time_zone IS NULL OR btrim(time_zone) = '') AND country = 'Sri Lanka';
UPDATE lms_student_profiles SET time_zone = 'Asia/Tokyo'
WHERE (time_zone IS NULL OR btrim(time_zone) = '') AND country = 'Japan';

ALTER TABLE lms_student_profiles
    ALTER COLUMN country SET DEFAULT 'Sri Lanka',
    ALTER COLUMN time_zone SET DEFAULT 'Asia/Colombo';
