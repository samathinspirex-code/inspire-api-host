ALTER TABLE lms_coursework_assignments DROP CONSTRAINT IF EXISTS chk_lms_coursework_submission_type
;
ALTER TABLE lms_coursework_assignments ADD CONSTRAINT chk_lms_coursework_submission_type
    CHECK (submission_type IN ('written', 'pdf_annotation', 'multimedia', 'coding', 'mcq'))
;
ALTER TABLE lms_exams DROP CONSTRAINT IF EXISTS ck_lms_exams_assessment_kind
;
ALTER TABLE lms_exams ADD CONSTRAINT ck_lms_exams_assessment_kind
    CHECK (assessment_kind IN ('exam', 'practice_test', 'graded_mcq'))
;
ALTER TABLE lms_coursework_submissions ADD COLUMN IF NOT EXISTS grade_band VARCHAR(20)
;
ALTER TABLE lms_exam_attempts ADD COLUMN IF NOT EXISTS grade_band VARCHAR(20)
;
