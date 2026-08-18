-- Run once against the existing Inspire database.
-- LMS remains the portal permission; exactly one role should also be assigned.
INSERT INTO access_levels (access_key, display_name, description)
VALUES
    ('SUPER_ADMIN', 'Super Admin', 'Full LMS configuration, reporting and user oversight'),
    ('ADMIN', 'Admin', 'Manage LMS academic data, people, classes and attendance'),
    ('LECTURER', 'Lecturer', 'Manage assigned courses, classes, meetings and attendance'),
    ('STUDENT', 'Student', 'Access enrolled courses, classes and learning progress')
ON CONFLICT (access_key) DO UPDATE
SET display_name = EXCLUDED.display_name,
    description = EXCLUDED.description,
    is_active = TRUE;
