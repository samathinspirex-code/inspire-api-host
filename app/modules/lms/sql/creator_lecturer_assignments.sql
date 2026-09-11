-- Give lecturer-creators ownership of reusable Course pages and class intakes
-- that were created before automatic creator assignment was introduced.
INSERT INTO lms_course_lecturers (course_id, lecturer_user_id, assigned_by)
SELECT course.course_id, course.created_by, course.created_by
FROM lms_courses course
JOIN lms_lecturer_profiles lecturer ON lecturer.user_id = course.created_by
WHERE course.created_by IS NOT NULL
ON CONFLICT DO NOTHING;

INSERT INTO lms_class_lecturers (class_id, lecturer_user_id, assigned_by)
SELECT intake.class_id, intake.created_by, intake.created_by
FROM lms_classes intake
JOIN lms_lecturer_profiles lecturer ON lecturer.user_id = intake.created_by
WHERE intake.created_by IS NOT NULL
ON CONFLICT DO NOTHING;
