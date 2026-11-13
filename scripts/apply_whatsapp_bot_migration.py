"""CRM -> WhatsApp Bot: sync state tables. Safe to re-run.

Access comes from CRM & Admissions and Admissions Counsellor, so the earlier separate
WHATSAPP_BOT access level is removed from every user and switched off.
"""

import asyncio
import sys
from pathlib import Path

from sqlalchemy import text

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.core.database import engine

SQL = REPO_ROOT / "app" / "modules" / "whatsapp_bot" / "sql" / "2026_11_13_wabot_sync.sql"


async def main() -> None:
    engine.echo = False
    try:
        async with engine.begin() as connection:
            # Drop comment lines before splitting, so a ";" inside a comment can't break a statement.
            sql = "\n".join(line for line in SQL.read_text(encoding="utf-8").splitlines() if not line.strip().startswith("--"))
            for statement in [s.strip() for s in sql.split(";")]:
                if statement:
                    await connection.execute(text(statement))
            removed = (await connection.execute(text(
                "DELETE FROM user_access_levels WHERE access_level_id IN "
                "(SELECT access_level_id FROM access_levels WHERE access_key = 'WHATSAPP_BOT')"
            ))).rowcount
            await connection.execute(text("UPDATE access_levels SET is_active = false WHERE access_key = 'WHATSAPP_BOT'"))
        print(f"WhatsApp bot sync tables are ready. Removed the old WhatsApp Bot role from {removed} user(s).")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
