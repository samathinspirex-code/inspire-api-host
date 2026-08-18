from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.cms.models import Outcome


class OutcomeRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_by_program(self, program_id: int) -> list[Outcome]:
        stmt = select(Outcome).where(Outcome.program_id == program_id).order_by(Outcome.order)
        return list((await self.db.execute(stmt)).scalars().all())

    async def create(self, program_id: int, outcome_text: str) -> Outcome:
        max_order_stmt = select(func.max(Outcome.order)).where(Outcome.program_id == program_id)
        max_order = (await self.db.execute(max_order_stmt)).scalar_one()
        outcome = Outcome(program_id=program_id, order=(max_order or 0) + 1, outcome=outcome_text)
        self.db.add(outcome)
        await self.db.commit()
        await self.db.refresh(outcome)
        return outcome

    async def get(self, outcome_id: int) -> Optional[Outcome]:
        return await self.db.get(Outcome, outcome_id)

    async def update(self, outcome: Outcome, outcome_text: str) -> Outcome:
        outcome.outcome = outcome_text
        await self.db.commit()
        await self.db.refresh(outcome)
        return outcome

    async def delete_and_renumber(self, outcome: Outcome) -> None:
        program_id = outcome.program_id
        await self.db.delete(outcome)
        await self.db.flush()

        remaining = await self.list_by_program(program_id)
        for index, remaining_outcome in enumerate(remaining, start=1):
            if remaining_outcome.order != index:
                remaining_outcome.order = index

        await self.db.commit()

    async def reorder(self, outcomes: list[Outcome], outcome_ids: list[int]) -> list[Outcome]:
        by_id = {outcome.outcome_id: outcome for outcome in outcomes}

        # Two-phase update avoids UNIQUE (program_id, "order") collisions mid-transaction.
        for outcome in outcomes:
            outcome.order = -outcome.order
        await self.db.flush()

        for index, outcome_id in enumerate(outcome_ids, start=1):
            by_id[outcome_id].order = index
        await self.db.commit()

        return sorted(outcomes, key=lambda outcome: outcome.order)
