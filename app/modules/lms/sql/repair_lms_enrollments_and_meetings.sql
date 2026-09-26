-- Fix and backfill existing LMS data so lecturers and students can see all relevant
-- courses (templates), classes, meetings, and calendar events for their enrollments.

-- 1. Ensure all online meetings have an audience entry for their primary class
INSERT INTO lms_meeting_audience_classes (meeting_id, class_id)
SELECT meeting_id, class_id
FROM lms_online_meetings
WHERE class_id IS NOT NULL
ON CONFLICT DO NOTHING;

-- 2. Backfill course lecturer assignments from class lecturer assignments
INSERT INTO lms_course_lecturers (course_id, lecturer_user_id, assigned_by)
SELECT DISTINCT cl.course_id, cll.lecturer_user_id, COALESCE(cll.assigned_by, cll.lecturer_user_id)
FROM lms_class_lecturers cll
JOIN lms_classes cl ON cl.class_id = cll.class_id
WHERE cl.course_id IS NOT NULL
ON CONFLICT DO NOTHING;

-- Also backfill for master template course if this is a class copy
INSERT INTO lms_course_lecturers (course_id, lecturer_user_id, assigned_by)
SELECT DISTINCT c.source_master_course_id, cll.lecturer_user_id, COALESCE(cll.assigned_by, cll.lecturer_user_id)
FROM lms_class_lecturers cll
JOIN lms_classes cl ON cl.class_id = cll.class_id
JOIN lms_courses c ON c.course_id = cl.course_id
WHERE c.source_master_course_id IS NOT NULL
ON CONFLICT DO NOTHING;

-- 3. Backfill course enrollment for all students assigned to classes
INSERT INTO lms_course_enrollments (course_id, student_user_id, status, enrolled_by)
SELECT DISTINCT cl.course_id, cs.student_user_id, 'enrolled', COALESCE(cs.assigned_by, cs.student_user_id)
FROM lms_class_students cs
JOIN lms_classes cl ON cl.class_id = cs.class_id
WHERE cl.course_id IS NOT NULL
ON CONFLICT (course_id, student_user_id)
DO UPDATE SET status = 'enrolled'
WHERE lms_course_enrollments.status != 'enrolled';

-- Also backfill course enrollment for master template courses
INSERT INTO lms_course_enrollments (course_id, student_user_id, status, enrolled_by)
SELECT DISTINCT c.source_master_course_id, cs.student_user_id, 'enrolled', COALESCE(cs.assigned_by, cs.student_user_id)
FROM lms_class_students cs
JOIN lms_classes cl ON cl.class_id = cs.class_id
JOIN lms_courses c ON c.course_id = cl.course_id
WHERE c.source_master_course_id IS NOT NULL
ON CONFLICT (course_id, student_user_id)
DO UPDATE SET status = 'enrolled'
WHERE lms_course_enrollments.status != 'enrolled';
