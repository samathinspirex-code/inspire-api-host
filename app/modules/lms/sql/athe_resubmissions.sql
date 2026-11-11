-- ATHE resubmissions: a Resubmission grade can carry a per-student deadline, and each resubmitted
-- attempt is marked on its own rubric forms so the earlier feedback stays on record.
ALTER TABLE lms_rubric_assessments ADD COLUMN IF NOT EXISTS attempt_no SMALLINT NOT NULL DEFAULT 1
;
ALTER TABLE lms_rubric_assessments ADD COLUMN IF NOT EXISTS resubmission_due_at TIMESTAMPTZ
;
ALTER TABLE lms_rubric_assessments DROP CONSTRAINT IF EXISTS uq_lms_rubric_assessments_submission_role
;
ALTER TABLE lms_rubric_assessments DROP CONSTRAINT IF EXISTS uq_lms_rubric_assessments_submission_role_attempt
;
ALTER TABLE lms_rubric_assessments
    ADD CONSTRAINT uq_lms_rubric_assessments_submission_role_attempt UNIQUE (submission_id, assessor_role, attempt_no)
;
CREATE TABLE IF NOT EXISTS lms_rubric_resubmissions (
    resubmission_id SERIAL PRIMARY KEY,
    assignment_id INTEGER NOT NULL REFERENCES lms_coursework_assignments(assignment_id) ON DELETE CASCADE,
    submission_id INTEGER NOT NULL REFERENCES lms_coursework_submissions(submission_id) ON DELETE CASCADE,
    student_user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    attempt_no SMALLINT NOT NULL CHECK (attempt_no >= 2),
    due_at TIMESTAMPTZ NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'submitted', 'marked', 'released')),
    answer_text TEXT,
    attachment_asset_id INTEGER REFERENCES media_assets(media_asset_id) ON DELETE SET NULL,
    submitted_at TIMESTAMPTZ,
    released_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_lms_rubric_resubmissions_attempt UNIQUE (submission_id, attempt_no)
)
;
CREATE INDEX IF NOT EXISTS idx_lms_rubric_resubmissions_assignment ON lms_rubric_resubmissions (assignment_id, status)
;
CREATE INDEX IF NOT EXISTS idx_lms_rubric_resubmissions_student ON lms_rubric_resubmissions (student_user_id, status)
