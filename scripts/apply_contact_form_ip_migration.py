"""Add server-observed contact form IP fields without changing old leads."""
import asyncio
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import settings  # noqa: E402


async def main() -> None:
    sql = (Path(__file__).resolve().parents[1] / "app/modules/crm/sql/contact_form_ip.sql").read_text(encoding="utf-8")
    connection = await asyncpg.connect(
        user=settings.POSTGRES_USER, password=settings.POSTGRES_PASSWORD,
        host=settings.POSTGRES_HOST, port=settings.POSTGRES_PORT, database=settings.POSTGRES_DB,
        ssl="require" if settings.POSTGRES_HOST not in {"localhost", "127.0.0.1"} else None,
    )
    try:
        await connection.execute(sql)
        columns = await connection.fetchval("""SELECT count(*) FROM information_schema.columns
          WHERE table_schema='public' AND table_name='crm_leads'
            AND column_name IN ('contact_visitor_ip','contact_peer_ip')""")
    finally:
        await connection.close()
    if columns != 2:
        raise RuntimeError("Contact IP columns were not created")
    print("Contact form IP migration applied.")


if __name__ == "__main__":
    asyncio.run(main())
