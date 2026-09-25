"""Fix foreign key constraints on lms_online_meetings and assignment tables to reference users(user_id) instead of profile tables."""
import asyncio
from sqlalchemy import text
from app.core.database import engine


async def main() -> None:
    async with engine.begin() as connection:
        # Find all foreign key constraints on lms_online_meetings targeting lecturer_user_id
        await connection.execute(text("""
            DO $$
            DECLARE
                r RECORD;
            BEGIN
                FOR r IN (
                    SELECT tc.constraint_name
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                      ON tc.constraint_name = kcu.constraint_name
                     AND tc.table_schema = kcu.table_schema
                    WHERE tc.constraint_type = 'FOREIGN KEY'
                      AND tc.table_name = 'lms_online_meetings'
                      AND kcu.column_name = 'lecturer_user_id'
                ) LOOP
                    EXECUTE 'ALTER TABLE lms_online_meetings DROP CONSTRAINT ' || quote_ident(r.constraint_name);
                END LOOP;
            END $$;
        """))
        await connection.execute(text("""
            ALTER TABLE lms_online_meetings
            ADD CONSTRAINT lms_online_meetings_lecturer_user_id_fkey
            FOREIGN KEY (lecturer_user_id) REFERENCES users(user_id) ON DELETE CASCADE;
        """))

        # Also fix lms_course_lecturers and lms_class_lecturers
        await connection.execute(text("""
            DO $$
            DECLARE
                r RECORD;
            BEGIN
                FOR r IN (
                    SELECT tc.table_name, tc.constraint_name
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                      ON tc.constraint_name = kcu.constraint_name
                     AND tc.table_schema = kcu.table_schema
                    WHERE tc.constraint_type = 'FOREIGN KEY'
                      AND tc.table_name IN ('lms_course_lecturers', 'lms_class_lecturers')
                      AND kcu.column_name = 'lecturer_user_id'
                ) LOOP
                    EXECUTE 'ALTER TABLE ' || quote_ident(r.table_name) || ' DROP CONSTRAINT ' || quote_ident(r.constraint_name);
                END LOOP;
            END $$;
        """))
        await connection.execute(text("""
            ALTER TABLE lms_course_lecturers
            ADD CONSTRAINT lms_course_lecturers_lecturer_user_id_fkey
            FOREIGN KEY (lecturer_user_id) REFERENCES users(user_id) ON DELETE CASCADE;
        """))
        await connection.execute(text("""
            ALTER TABLE lms_class_lecturers
            ADD CONSTRAINT lms_class_lecturers_lecturer_user_id_fkey
            FOREIGN KEY (lecturer_user_id) REFERENCES users(user_id) ON DELETE CASCADE;
        """))

        # Also fix lms_course_enrollments and lms_class_students
        await connection.execute(text("""
            DO $$
            DECLARE
                r RECORD;
            BEGIN
                FOR r IN (
                    SELECT tc.table_name, tc.constraint_name
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                      ON tc.constraint_name = kcu.constraint_name
                     AND tc.table_schema = kcu.table_schema
                    WHERE tc.constraint_type = 'FOREIGN KEY'
                      AND tc.table_name IN ('lms_course_enrollments', 'lms_class_students')
                      AND kcu.column_name = 'student_user_id'
                ) LOOP
                    EXECUTE 'ALTER TABLE ' || quote_ident(r.table_name) || ' DROP CONSTRAINT ' || quote_ident(r.constraint_name);
                END LOOP;
            END $$;
        """))
        await connection.execute(text("""
            ALTER TABLE lms_course_enrollments
            ADD CONSTRAINT lms_course_enrollments_student_user_id_fkey
            FOREIGN KEY (student_user_id) REFERENCES users(user_id) ON DELETE CASCADE;
        """))
        await connection.execute(text("""
            ALTER TABLE lms_class_students
            ADD CONSTRAINT lms_class_students_student_user_id_fkey
            FOREIGN KEY (student_user_id) REFERENCES users(user_id) ON DELETE CASCADE;
        """))
        print("Successfully updated foreign key constraints to reference users(user_id).")


if __name__ == "__main__":
    asyncio.run(main())
