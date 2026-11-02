"""Preview or migrate legacy CRM outcome stages into Not Interested.

Run without flags for counts only. Run with --apply only after the CRM release
is approved; the migration records the prior outcome and is safe to rerun.
"""

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import engine


async def main(apply: bool) -> None:
    engine.echo = False
    async with engine.begin() as connection:
        rows = (await connection.execute(text(
            "SELECT stage, count(*) FROM crm_leads "
            "WHERE stage IN ('lost_to_competitor', 'cant_afford') GROUP BY stage ORDER BY stage"
        ))).all()
        print(dict(rows))
        if not apply or not rows:
            return
        await connection.execute(text("""
            UPDATE crm_leads
            SET notes = concat_ws(E'\\n', nullif(notes, ''),
                'Previous CRM stage: ' || stage ||
                CASE WHEN nullif(status_reason, '') IS NOT NULL THEN '; previous reason: ' || status_reason ELSE '' END ||
                CASE WHEN nullif(affordability_reason, '') IS NOT NULL THEN '; affordability reason: ' || affordability_reason ELSE '' END ||
                CASE WHEN nullif(status_remarks, '') IS NOT NULL THEN '; remarks: ' || status_remarks ELSE '' END),
                status_reason = CASE WHEN stage = 'lost_to_competitor' THEN 'Joined Another Institute'
                                     ELSE 'Fees Beyond Expectations' END,
                stage = 'not_interested'
            WHERE stage IN ('lost_to_competitor', 'cant_afford')
        """))
        print("Legacy outcomes migrated to Not Interested.")
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Update the configured database")
    asyncio.run(main(parser.parse_args().apply))
