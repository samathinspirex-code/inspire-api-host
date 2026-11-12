"""Reusable Course pages expose the pathway they carry into new classes."""

import unittest
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.modules.lms.portal_service import _academic_course_pathways


class AsyncSessionAdapter:
    def __init__(self, session):
        self.session = session

    async def execute(self, statement, params=None):
        return self.session.execute(statement, params or {})


class CourseTemplatePathwayTests(unittest.IsolatedAsyncioTestCase):
    async def test_template_uses_lowest_linked_academic_course_and_skips_orientation(self):
        engine = create_engine("sqlite:///:memory:")
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE lms_courses (course_id INTEGER PRIMARY KEY, program_id INTEGER)")
            connection.exec_driver_sql("""
                CREATE TABLE academic_courses (
                    course_id INTEGER PRIMARY KEY, legacy_program_id INTEGER,
                    awarding_body TEXT, programme_id INTEGER, school_id INTEGER
                )
            """)
            connection.exec_driver_sql("CREATE TABLE academic_programmes (programme_id INTEGER PRIMARY KEY, name TEXT)")
            connection.exec_driver_sql("CREATE TABLE academic_schools (school_id INTEGER PRIMARY KEY, name TEXT)")
            connection.exec_driver_sql("INSERT INTO academic_programmes VALUES (1, 'HND'), (2, 'Level 3')")
            connection.exec_driver_sql("INSERT INTO academic_schools VALUES (1, 'School of Business'), (2, 'School of Computing')")
            connection.exec_driver_sql("""
                INSERT INTO academic_courses VALUES
                  (9, 4, 'Pearson', 2, 2),
                  (3, 4, 'ATHE', 1, 1),
                  (5, 7, 'ATHE', 2, 2)
            """)
            connection.exec_driver_sql("INSERT INTO lms_courses VALUES (20, 4), (21, 7), (22, 99), (23, NULL)")

        def course(course_id, program_id, is_orientation=False):
            return (SimpleNamespace(course_id=course_id, program_id=program_id, is_orientation=is_orientation),)

        with Session(engine) as session:
            pathways = await _academic_course_pathways(AsyncSessionAdapter(session), [
                course(20, 4), course(21, 7), course(22, 99), course(23, None, True),
            ])

        self.assertEqual(pathways[20], {"course_id": 20, "awarding_body": "ATHE", "school_name": "School of Business", "programme_name": "HND"})
        self.assertEqual(pathways[21]["school_name"], "School of Computing")
        self.assertNotIn(22, pathways)
        self.assertNotIn(23, pathways)


if __name__ == "__main__":
    unittest.main()
