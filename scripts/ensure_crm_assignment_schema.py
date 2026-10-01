import asyncio

from sqlalchemy import text

from app.core.database import engine


async def main() -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS crm_counsellor_status ("
                "user_id INTEGER PRIMARY KEY REFERENCES users(user_id), "
                "is_active BOOLEAN NOT NULL DEFAULT true, "
                "updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()"
                ")"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE crm_counsellor_status "
                "ADD COLUMN IF NOT EXISTS assign_order INTEGER NOT NULL DEFAULT 0"
            )
        )
        await conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS crm_assignment_settings ("
                "id INTEGER PRIMARY KEY, "
                "auto_assign_enabled BOOLEAN NOT NULL DEFAULT true, "
                "last_counsellor_id INTEGER, "
                "last_assigned_on DATE, "
                "updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()"
                ")"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO crm_assignment_settings (id, auto_assign_enabled) "
                "VALUES (1, true) ON CONFLICT (id) DO NOTHING"
            )
        )
        await conn.execute(
            text("ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS awarding_body VARCHAR(100)")
        )
        print("crm assignment schema ready")


if __name__ == "__main__":
    asyncio.run(main())
