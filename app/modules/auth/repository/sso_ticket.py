from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import SsoTicket


class SsoTicketRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, user_id: int, ticket_hash: str, expires_at: datetime) -> SsoTicket:
        ticket = SsoTicket(user_id=user_id, ticket_hash=ticket_hash, expires_at=expires_at)
        self.db.add(ticket)
        await self.db.commit()
        return ticket

    async def get_for_update(self, ticket_hash: str) -> SsoTicket | None:
        stmt = select(SsoTicket).where(SsoTicket.ticket_hash == ticket_hash).with_for_update()
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def mark_used(self, ticket: SsoTicket) -> None:
        ticket.used_at = datetime.now(timezone.utc)
        await self.db.commit()
