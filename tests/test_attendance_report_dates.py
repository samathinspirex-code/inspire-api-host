import unittest
from datetime import date

from sqlalchemy.dialects import postgresql

from app.modules.lms.repository.attendance import AttendanceRepository


class AttendanceReportDateTests(unittest.TestCase):
    def test_date_filters_use_the_class_local_day(self):
        filters = AttendanceRepository._report_conditions(
            date_from=date(2026, 10, 9), date_to=date(2026, 10, 11)
        )
        sql = " ".join(str(item.compile(dialect=postgresql.dialect())) for item in filters)
        self.assertIn("timezone(lms_classes.timezone, lms_online_meetings.start_time)", sql)


if __name__ == "__main__":
    unittest.main()
