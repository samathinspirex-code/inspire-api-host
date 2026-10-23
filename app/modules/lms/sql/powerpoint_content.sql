-- Expand the content type constraint without changing existing learning items.
DO $$
DECLARE
    constraint_row RECORD;
BEGIN
    FOR constraint_row IN
        SELECT c.conname FROM pg_constraint c
        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
        WHERE c.conrelid = 'lms_learning_items'::regclass
          AND c.contype = 'c' AND a.attname = 'item_type'
    LOOP
        EXECUTE format('ALTER TABLE lms_learning_items DROP CONSTRAINT %I', constraint_row.conname);
    END LOOP;
    ALTER TABLE lms_learning_items ADD CONSTRAINT lms_learning_items_item_type_check
        CHECK (item_type IN ('video', 'pdf', 'pptx', 'text', 'link', 'assignment', 'quiz'));
END $$;
