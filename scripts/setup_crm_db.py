"""Create missing CRM tables without adding sample leads."""
import asyncio

from sqlalchemy import text
from app.core.database import Base, engine
from app.modules.crm.models.lead import CrmLead  # noqa: F401
from app.modules.crm.models.activity import CrmActivity  # noqa: F401


async def setup_crm():
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        count = await connection.scalar(text("SELECT count(*) FROM crm_leads"))
        print(f"CRM tables ready; existing lead count: {count}")


if __name__ == "__main__":
    asyncio.run(setup_crm())
