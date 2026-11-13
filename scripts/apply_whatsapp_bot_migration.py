"""CRM -> WhatsApp Bot: sync state tables and the WHATSAPP_BOT access level. Safe to re-run.

The access level is created but granted to nobody; grant it in People & Access.
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
            await connection.execute(text(
                "INSERT INTO access_levels (access_key, display_name, description, is_active) "
                "VALUES ('WHATSAPP_BOT', 'WhatsApp Bot', 'Access the WhatsApp Bot section in CRM', true) "
                "ON CONFLICT (access_key) DO UPDATE SET display_name = EXCLUDED.display_name, "
                "description = EXCLUDED.description, is_active = true"
            ))
        print("WhatsApp bot sync tables and WHATSAPP_BOT access level are ready.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
