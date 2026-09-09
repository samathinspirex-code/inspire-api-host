ALTER TABLE lms_coursework_assignments
    ADD COLUMN IF NOT EXISTS learning_item_id BIGINT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_coursework_assignment_learning_item'
    ) THEN
        ALTER TABLE lms_coursework_assignments
            ADD CONSTRAINT fk_coursework_assignment_learning_item
            FOREIGN KEY (learning_item_id) REFERENCES lms_learning_items(learning_item_id) ON DELETE CASCADE;
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_coursework_assignment_learning_item
    ON lms_coursework_assignments (learning_item_id)
    WHERE learning_item_id IS NOT NULL;

ALTER TABLE lms_exams
    ADD COLUMN IF NOT EXISTS assessment_kind VARCHAR(20) NOT NULL DEFAULT 'exam';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_lms_exams_assessment_kind'
    ) THEN
        ALTER TABLE lms_exams
            ADD CONSTRAINT ck_lms_exams_assessment_kind
            CHECK (assessment_kind IN ('exam', 'practice_test'));
    END IF;
END $$;

ALTER TABLE lms_exam_questions
    ADD COLUMN IF NOT EXISTS correct_option_indices JSONB;

ALTER TABLE lms_exam_questions DROP CONSTRAINT IF EXISTS lms_exam_questions_question_type_check;
ALTER TABLE lms_exam_questions DROP CONSTRAINT IF EXISTS lms_exam_questions_check;
ALTER TABLE lms_exam_questions ADD CONSTRAINT lms_exam_questions_question_type_check
    CHECK (question_type IN ('mcq', 'multiple_answer', 'short_answer', 'essay'));

WITH inserted AS (
    INSERT INTO lms_modules (course_id, title, description, position, status, created_by)
    SELECT c.course_id,
           'Practice Test',
           'Multiple-choice practice tests added after online classes.',
           COALESCE((SELECT MAX(m.position) FROM lms_modules m WHERE m.course_id = c.course_id), 0) + 1,
           'active',
           c.created_by
    FROM lms_courses c
    WHERE NOT EXISTS (
        SELECT 1 FROM lms_modules m
        WHERE m.course_id = c.course_id AND lower(trim(m.title)) = 'practice test'
    )
    RETURNING module_id, course_id, created_by
)
INSERT INTO lms_module_access (module_id, scope_type, scope_id, is_unlocked, created_by)
SELECT module_id, 'course', course_id, TRUE, created_by FROM inserted
ON CONFLICT (module_id, scope_type, scope_id) DO UPDATE SET is_unlocked = TRUE;

INSERT INTO lms_module_access (module_id, scope_type, scope_id, is_unlocked, created_by)
SELECT m.module_id, 'course', m.course_id, TRUE, m.created_by
FROM lms_modules m
WHERE lower(trim(m.title)) = 'practice test'
ON CONFLICT (module_id, scope_type, scope_id) DO UPDATE SET is_unlocked = TRUE;
