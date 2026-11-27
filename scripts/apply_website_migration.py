"""Create the new website's tables (website content, demo LMS, website checkout). Additive and safe to rerun.

Run on the server after taking a database backup:
    python scripts/apply_website_migration.py
Requires the payments schema (scripts/apply_payments_migration.py) to exist already.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.modules.payments.db import connect  # noqa: E402

SQL = Path(__file__).resolve().parents[1] / "app/modules/website/sql/001_website.sql"


async def main() -> None:
    c = await connect()
    try:
        if not await c.fetchval("SELECT to_regclass('payments.learners') IS NOT NULL"):
            raise SystemExit("The payments schema is missing. Run scripts/apply_payments_migration.py first.")
        async with c.transaction():
            await c.execute("SET LOCAL lock_timeout = '5s'")
            await c.execute(SQL.read_text(encoding="utf-8"))
        tables = await c.fetch("""SELECT table_schema||'.'||table_name AS t FROM information_schema.tables
            WHERE (table_schema='public' AND (table_name LIKE 'website\\_%' OR table_name LIKE 'web\\_demo\\_%'))
               OR (table_schema='payments' AND table_name IN ('web_orders','web_checkouts','web_enrolments','bank_slips')) ORDER BY 1""")
        print("New website tables ready:", ", ".join(r["t"] for r in tables))
    finally:
        await c.close()


if __name__ == "__main__":
    asyncio.run(main())
