"""Keep only the newest CRM leads, with a recoverable database backup."""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import engine


async def run(keep: int, apply: bool) -> None:
    if keep < 1:
        raise ValueError("--keep must be at least 1")

    async with engine.begin() as connection:
        total = int(await connection.scalar(text("SELECT COUNT(*) FROM crm_leads")) or 0)
        remove = max(0, total - keep)
        boundary = (
            await connection.execute(
                text(
                    "SELECT lead_id, full_name, created_at FROM crm_leads "
                    "ORDER BY created_at DESC NULLS LAST, lead_id DESC "
                    "OFFSET :offset LIMIT 1"
                ),
                {"offset": max(0, keep - 1)},
            )
        ).mappings().one_or_none()

        print(f"Current CRM leads: {total:,}")
        print(f"Newest leads to keep: {min(keep, total):,}")
        print(f"Older leads to remove: {remove:,}")
        if boundary:
            print(
                "Oldest retained lead: "
                f"#{boundary['lead_id']} {boundary['full_name']} ({boundary['created_at']})"
            )

        if not apply or remove == 0:
            print("Dry run only; no CRM records were changed.")
            return

        suffix = re.sub(r"[^0-9]", "", datetime.now().isoformat(timespec="seconds"))
        lead_backup = f"crm_leads_backup_{suffix}_keep_{keep}"
        activity_backup = f"crm_activities_backup_{suffix}_keep_{keep}"

        await connection.execute(
            text(
                f"CREATE TABLE {lead_backup} AS "
                "WITH retained AS ("
                " SELECT lead_id FROM crm_leads"
                " ORDER BY created_at DESC NULLS LAST, lead_id DESC LIMIT :keep"
                ") SELECT lead.* FROM crm_leads lead "
                "WHERE NOT EXISTS (SELECT 1 FROM retained WHERE retained.lead_id = lead.lead_id)"
            ),
            {"keep": keep},
        )
        await connection.execute(
            text(
                f"CREATE TABLE {activity_backup} AS "
                "SELECT activity.* FROM crm_activities activity "
                f"JOIN {lead_backup} removed ON removed.lead_id = activity.lead_id"
            )
        )
        await connection.execute(
            text(
                f"DELETE FROM crm_leads lead USING {lead_backup} removed "
                "WHERE lead.lead_id = removed.lead_id"
            )
        )

        remaining = int(await connection.scalar(text("SELECT COUNT(*) FROM crm_leads")) or 0)
        backed_up = int(
            await connection.scalar(text(f"SELECT COUNT(*) FROM {lead_backup}")) or 0
        )
        print(f"Remaining CRM leads: {remaining:,}")
        print(f"Backed-up removed leads: {backed_up:,}")
        print(f"Lead backup table: {lead_backup}")
        print(f"Activity backup table: {activity_backup}")

    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", type=int, default=600)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.keep, args.apply))


if __name__ == "__main__":
    main()
