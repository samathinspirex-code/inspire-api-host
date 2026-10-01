import asyncio

from sqlalchemy import text

from app.core.database import engine


async def main() -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text("ALTER TABLE crm_leads ADD COLUMN IF NOT EXISTS awarding_body VARCHAR(100)")
        )
        exists = await conn.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='crm_leads' AND column_name='awarding_body')"
            )
        )
        print(f"awarding_body exists: {exists}")


if __name__ == "__main__":
    asyncio.run(main())
