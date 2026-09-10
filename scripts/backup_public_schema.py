"""Create a timestamped, data-only safety snapshot inside the configured PostgreSQL database."""
import asyncio
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import settings  # noqa: E402


def quote_identifier(value: str) -> str:
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", value):
        raise ValueError(f"Unsafe SQL identifier: {value}")
    return f'"{value}"'


async def main():
    suffix = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_schema = f"backup_pre_academic_{suffix}"
    connection = await asyncpg.connect(
        user=settings.POSTGRES_USER, password=settings.POSTGRES_PASSWORD,
        host=settings.POSTGRES_HOST, port=settings.POSTGRES_PORT, database=settings.POSTGRES_DB,
        ssl="require" if settings.POSTGRES_HOST not in {"localhost", "127.0.0.1"} else None,
    )
    try:
        tables = [row["table_name"] for row in await connection.fetch("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name
        """)]
        async with connection.transaction():
            await connection.execute(f"CREATE SCHEMA {quote_identifier(backup_schema)}")
            for table in tables:
                await connection.execute(
                    f"CREATE TABLE {quote_identifier(backup_schema)}.{quote_identifier(table)} "
                    f"AS TABLE public.{quote_identifier(table)}"
                )
            await connection.execute(
                f"CREATE TABLE {quote_identifier(backup_schema)}.backup_manifest "
                "(created_at timestamptz NOT NULL, source_schema text NOT NULL, table_count int NOT NULL)"
            )
            await connection.execute(
                f"INSERT INTO {quote_identifier(backup_schema)}.backup_manifest VALUES (now(), 'public', $1)", len(tables)
            )
        print(f"Supabase safety snapshot created: {backup_schema} ({len(tables)} tables)")
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(main())
