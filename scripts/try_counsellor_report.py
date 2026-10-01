import asyncio
from datetime import date

from app.core.database import AsyncSessionLocal
from app.modules.crm import service


async def main() -> None:
    async with AsyncSessionLocal() as db:
        try:
            report = await service.get_counsellor_report(db, date.today(), date.today(), None)
            print("ok", report.date_from, report.date_to, "rows", len(report.counsellors), "new", report.totals.new_leads)
        except Exception as exc:
            print("REPORT ERR", type(exc).__name__, exc)


if __name__ == "__main__":
    asyncio.run(main())
