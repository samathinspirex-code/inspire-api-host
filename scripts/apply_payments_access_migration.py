"""Initialize Payments access for the designated account only. Run once at rollout."""

import asyncio
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import engine


async def main() -> None:
    try:
        async with engine.begin() as connection:
            user_id = (await connection.execute(text(
                "SELECT user_id FROM users WHERE lower(email) = :email"
            ), {"email": "samathinspirex@gmail.com"})).scalar_one()
            finance_id = (await connection.execute(text(
                "INSERT INTO access_levels (access_key, display_name, description, is_active) "
                "VALUES ('FINANCE', 'Payments', 'Access the Payments section', true) "
                "ON CONFLICT (access_key) DO UPDATE SET display_name = EXCLUDED.display_name, "
                "description = EXCLUDED.description, is_active = true RETURNING access_level_id"
            ))).scalar_one()
            await connection.execute(text(
                "DELETE FROM user_access_levels WHERE access_level_id = :finance_id AND user_id <> :user_id"
            ), {"finance_id": finance_id, "user_id": user_id})
            await connection.execute(text(
                "INSERT INTO user_access_levels (user_id, access_level_id) VALUES (:user_id, :finance_id) "
                "ON CONFLICT (user_id, access_level_id) DO NOTHING"
            ), {"user_id": user_id, "finance_id": finance_id})
            holders = (await connection.execute(text(
                "SELECT u.email FROM users u JOIN user_access_levels ua ON ua.user_id = u.user_id "
                "WHERE ua.access_level_id = :finance_id"
            ), {"finance_id": finance_id})).scalars().all()
            if holders != ["samathinspirex@gmail.com"] and [email.lower() for email in holders] != ["samathinspirex@gmail.com"]:
                raise RuntimeError("Unexpected Payments access holders; rolling back")
        print("Payments access verified: samathinspirex@gmail.com only.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
