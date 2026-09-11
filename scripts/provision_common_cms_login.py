"""Create or reactivate the configured shared CMS account and grant CMS access."""
import asyncio
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import settings  # noqa: E402


async def provision() -> None:
    email = settings.CMS_COMMON_LOGIN_EMAIL.strip().lower()
    if not email or not settings.CMS_COMMON_LOGIN_CODE.strip():
        raise RuntimeError("Set CMS_COMMON_LOGIN_EMAIL and CMS_COMMON_LOGIN_CODE first.")

    connection = await asyncpg.connect(
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        database=settings.POSTGRES_DB,
        ssl="require" if settings.POSTGRES_HOST not in {"localhost", "127.0.0.1"} else None,
    )
    try:
        async with connection.transaction():
            user_id = await connection.fetchval(
                "SELECT user_id FROM users WHERE lower(email) = $1 LIMIT 1", email
            )
            if user_id is None:
                user_id = await connection.fetchval(
                    """
                    INSERT INTO users (email, full_name, is_active)
                    VALUES ($1, 'Common CMS User', TRUE)
                    RETURNING user_id
                    """,
                    email,
                )
            else:
                await connection.execute(
                    "UPDATE users SET is_active = TRUE, updated_at = now() WHERE user_id = $1",
                    user_id,
                )

            access_level_id = await connection.fetchval(
                """
                INSERT INTO access_levels (access_key, display_name, description, is_active)
                VALUES ('CMS', 'CMS', 'Access to course and website content management', TRUE)
                ON CONFLICT (access_key) DO UPDATE SET is_active = TRUE
                RETURNING access_level_id
                """
            )
            await connection.execute(
                """
                INSERT INTO user_access_levels (user_id, access_level_id)
                VALUES ($1, $2)
                ON CONFLICT (user_id, access_level_id) DO NOTHING
                """,
                user_id,
                access_level_id,
            )
    finally:
        await connection.close()
    print(f"Common CMS login provisioned for {email}.")


if __name__ == "__main__":
    asyncio.run(provision())
