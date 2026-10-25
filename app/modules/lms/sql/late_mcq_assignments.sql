-- Timed MCQs may accept late starts; other timed assignment types remain closed.
-- Replace only the earlier timed/allow_late rule. Existing assignments are valid.
DO $$
DECLARE
    old_constraint RECORD;
BEGIN
    FOR old_constraint IN
        SELECT conname
        FROM pg_constraint
        WHERE conrelid = 'lms_coursework_assignments'::regclass
          AND contype = 'c'
          AND pg_get_constraintdef(oid) ILIKE '%assignment_type%'
          AND pg_get_constraintdef(oid) ILIKE '%allow_late%'
          AND (conname = 'chk_lms_coursework_timed_late'
               OR pg_get_constraintdef(oid) NOT ILIKE '%submission_type%')
    LOOP
        EXECUTE format('ALTER TABLE lms_coursework_assignments DROP CONSTRAINT %I', old_constraint.conname);
    END LOOP;

    ALTER TABLE lms_coursework_assignments
        ADD CONSTRAINT chk_lms_coursework_timed_late
        CHECK (assignment_type <> 'timed' OR allow_late = FALSE OR submission_type = 'mcq');
END $$;
