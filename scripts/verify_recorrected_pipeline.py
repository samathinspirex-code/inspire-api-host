"""Read-only reconciliation for the reviewed Recorrected pipeline import."""

import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.database import engine


PREFIX = "recorrected-3f7ca71d2643-row-%"


async def main():
    async with engine.connect() as db:
        summary = (await db.execute(text("""
            SELECT count(*) AS leads,
                   min(created_at) AS first_created,
                   max(created_at) AS last_created,
                   count(*) FILTER (WHERE phone IS NULL OR phone='') AS blank_phone,
                   count(*) FILTER (WHERE assigned_counsellor_id IS NULL) AS unassigned,
                   count(*) FILTER (WHERE stage='enrolled' AND email IS NULL
                     AND enrollment_email_status='pending_email_legacy') AS legacy_enrolled,
                   count(*) FILTER (WHERE enrollment_email_sent_at IS NOT NULL) AS enrollment_emails_sent,
                   count(*) FILTER (WHERE interested_course ILIKE '%(unavailable)%') AS unavailable_labels,
                   count(*) FILTER (WHERE notes LIKE '%Need add to website%') AS website_later_labels
            FROM crm_leads WHERE external_record_id LIKE :prefix
        """), {"prefix": PREFIX})).mappings().one()
        stages = (await db.execute(text("""
            SELECT stage, count(*) AS count FROM crm_leads
            WHERE external_record_id LIKE :prefix GROUP BY stage ORDER BY stage
        """), {"prefix": PREFIX})).mappings().all()
        owners = (await db.execute(text("""
            SELECT counsellor_name, count(*) AS count FROM crm_leads
            WHERE external_record_id LIKE :prefix
            GROUP BY counsellor_name ORDER BY count DESC
        """), {"prefix": PREFIX})).mappings().all()
        profiles = (await db.execute(text("""
            SELECT u.full_name, u.email, u.is_active, cs.is_active AS in_rotation
            FROM users u JOIN crm_counsellor_status cs ON cs.user_id=u.user_id
            WHERE u.email LIKE '%.legacy@example.com' ORDER BY u.full_name
        """))).mappings().all()
        print(json.dumps({
            "summary": dict(summary),
            "stages": [dict(row) for row in stages],
            "owners": [dict(row) for row in owners],
            "profiles": [dict(row) for row in profiles],
        }, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
