"""Dashboard period totals include only leads created in the selected days."""
import unittest
from datetime import date, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.modules.crm.models.lead import CrmLead
from app.modules.crm.service import get_dashboard


class LocalSession:
    def __init__(self, session):
        self.session = session

    async def execute(self, statement):
        return self.session.execute(statement)


class CrmDashboardPeriodTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        self.addCleanup(self.engine.dispose)
        CrmLead.__table__.create(self.engine)
        self.session = Session(self.engine)
        self.addCleanup(self.session.close)
        self.db = LocalSession(self.session)

    async def test_selected_day_filters_cards_pipeline_and_recent_activity(self):
        self.session.add_all([
            CrmLead(lead_id=1, full_name="Earlier lead", created_at=datetime(2026, 9, 10, 9),
                    updated_at=datetime(2026, 9, 10, 9), stage="new_lead"),
            CrmLead(lead_id=2, full_name="Selected lead", created_at=datetime(2026, 10, 24, 11),
                    updated_at=datetime(2026, 10, 24, 11), stage="contacted"),
        ])
        self.session.commit()
        all_days = await get_dashboard(self.db)
        selected = await get_dashboard(self.db, date_from=date(2026, 10, 24), date_to=date(2026, 10, 24))
        self.assertEqual(all_days.stats.total_leads, 2)
        self.assertEqual(selected.stats.total_leads, 1)
        self.assertEqual(selected.stats.pipeline_counts.get("contacted"), 1)
        self.assertEqual(selected.stats.pipeline_counts.get("new_lead", 0), 0)
        self.assertEqual([lead.full_name for lead in selected.recent_leads], ["Selected lead"])
