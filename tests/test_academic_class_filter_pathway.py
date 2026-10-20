"""Class filters use the saved course pathway, including legacy class copies."""

import unittest
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.modules.academic.service import list_academic_classes
from app.modules.lms.portal_service import _academic_class_pathways


class AsyncSessionAdapter:
    def __init__(self, session):
        self.session = session

    async def execute(self, statement, params=None):
        return self.session.execute(statement, params or {})


class AcademicClassFilterPathwayTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_class_uses_source_pathway_and_new_class_uses_catalogue_choice(self):
        engine = create_engine("sqlite:///:memory:")
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            connection.exec_driver_sql("""
                CREATE TABLE lms_classes (
                    class_id INTEGER PRIMARY KEY, course_id INTEGER, code TEXT, name TEXT,
                    description TEXT, start_date TEXT, end_date TEXT, delivery_mode TEXT,
                    timezone TEXT, capacity INTEGER, status TEXT, academic_course_id INTEGER,
                    study_mode TEXT, source_template_version_id INTEGER,
                    last_synced_version_id INTEGER
                )
            """)
            connection.exec_driver_sql("""
                CREATE TABLE lms_courses (
                    course_id INTEGER PRIMARY KEY, program_id INTEGER, code TEXT, title TEXT,
                    source_master_course_id INTEGER, catalogue_course_id INTEGER,
                    is_orientation INTEGER
                )
            """)
            connection.exec_driver_sql("""
                CREATE TABLE academic_courses (
                    course_id INTEGER PRIMARY KEY, legacy_program_id INTEGER, code TEXT,
                    title TEXT, awarding_body TEXT, programme_id INTEGER, school_id INTEGER
                )
            """)
            connection.exec_driver_sql("CREATE TABLE academic_programmes (programme_id INTEGER PRIMARY KEY, name TEXT)")
            connection.exec_driver_sql("CREATE TABLE academic_schools (school_id INTEGER PRIMARY KEY, name TEXT)")
            connection.exec_driver_sql("CREATE TABLE lms_class_students (class_id INTEGER)")
            connection.exec_driver_sql("INSERT INTO academic_programmes VALUES (1, 'HND')")
            connection.exec_driver_sql("INSERT INTO academic_schools VALUES (1, 'School of Business'), (2, 'School of Computing')")
            connection.exec_driver_sql("""
                INSERT INTO academic_courses VALUES
                  (6, 6, 'BUS', 'Business Management', 'ATHE', 1, 1),
                  (4, 4, 'COMP', 'Data Analytics', 'ATHE', 1, 2)
            """)
            connection.exec_driver_sql("""
                INSERT INTO lms_courses VALUES
                  (15, 4, 'LEGACY', 'Data Preparation', NULL, NULL, 0),
                  (16, 4, 'NEW', 'Data Preparation', NULL, 6, 0)
            """)
            connection.exec_driver_sql("""
                INSERT INTO lms_classes
                  (class_id, course_id, code, name, status, academic_course_id)
                VALUES (15, 15, 'C15', 'Legacy class', 'active', 6),
                       (16, 16, 'C16', 'Explicit catalogue class', 'active', 6)
            """)

        with Session(engine) as session:
            db = AsyncSessionAdapter(session)
            rows = await list_academic_classes(db)
            portal_pathways = await _academic_class_pathways(db, [
                (SimpleNamespace(class_id=15),),
                (SimpleNamespace(class_id=16),),
            ])

        by_id = {row["class_id"]: row for row in rows}
        self.assertEqual(by_id[15]["school_name"], "School of Computing")
        self.assertEqual(by_id[15]["catalogue_course_code"], "COMP")
        self.assertEqual(by_id[16]["school_name"], "School of Business")
        self.assertEqual(by_id[16]["catalogue_course_code"], "BUS")
        self.assertEqual(portal_pathways[15]["school_name"], "School of Computing")
        self.assertEqual(portal_pathways[16]["school_name"], "School of Business")


if __name__ == "__main__":
    unittest.main()
