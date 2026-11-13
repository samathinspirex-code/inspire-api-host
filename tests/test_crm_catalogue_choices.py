import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.modules.crm import service


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


def test_inspire_internal_school_courses_and_new_programmes_are_available():
    course = {"course_id": 12, "title": "Inspire Course", "awarding_body": "Inspire College",
              "programme_id": 3, "programme_name": "Inspire Programme", "school_id": 8,
              "school_name": "Inspire College", "programme_fee": None, "duration": None}
    db = SimpleNamespace(execute=AsyncMock(side_effect=[_Rows([course]), _Rows([{"programme_id": 9, "programme_name": "New Programme"}])]))
    choices = asyncio.run(service.list_programme_choices(db))

    assert choices[0]["course_id"] == 12
    assert choices[1]["course_id"] is None
    assert choices[1]["programme_name"] == "New Programme"
    course_sql = str(db.execute.await_args_list[0].args[0])
    assert "'INSPIRE', 'JAIN', 'LSBF'" in course_sql


def test_crm_filters_include_catalogue_entries_before_first_lead():
    choices = [{"course_id": None, "title": "New Programme", "awarding_body": None,
                "programme_name": "New Programme"},
               {"course_id": 12, "title": "Inspire Course", "awarding_body": "Inspire College",
                "programme_name": "Inspire Programme"}]
    db = SimpleNamespace(execute=AsyncMock(return_value=_Rows([])))
    with (patch.object(service, "_ensure_awarding_body_column", new_callable=AsyncMock),
          patch.object(service, "list_programme_choices", new_callable=AsyncMock, return_value=choices)):
        filters = asyncio.run(service.list_lead_filters(db))

    assert "Inspire College" in filters.awarding_bodies
    assert "New Programme" in filters.programmes
    assert "Inspire Course" in filters.programmes
