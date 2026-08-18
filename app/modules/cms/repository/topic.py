from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.cms.models import Topic


class TopicRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_by_program(self, program_id: int) -> list[Topic]:
        stmt = select(Topic).where(Topic.program_id == program_id).order_by(Topic.order)
        return list((await self.db.execute(stmt)).scalars().all())

    async def create(self, program_id: int, topic_text: str) -> Topic:
        max_order_stmt = select(func.max(Topic.order)).where(Topic.program_id == program_id)
        max_order = (await self.db.execute(max_order_stmt)).scalar_one()
        topic = Topic(program_id=program_id, order=(max_order or 0) + 1, topic=topic_text)
        self.db.add(topic)
        await self.db.commit()
        await self.db.refresh(topic)
        return topic

    async def get(self, topic_id: int) -> Optional[Topic]:
        return await self.db.get(Topic, topic_id)

    async def update(self, topic: Topic, topic_text: str) -> Topic:
        topic.topic = topic_text
        await self.db.commit()
        await self.db.refresh(topic)
        return topic

    async def delete_and_renumber(self, topic: Topic) -> None:
        program_id = topic.program_id
        await self.db.delete(topic)
        await self.db.flush()

        remaining = await self.list_by_program(program_id)
        for index, remaining_topic in enumerate(remaining, start=1):
            if remaining_topic.order != index:
                remaining_topic.order = index

        await self.db.commit()

    async def reorder(self, topics: list[Topic], topic_ids: list[int]) -> list[Topic]:
        by_id = {topic.topic_id: topic for topic in topics}

        # Two-phase update avoids UNIQUE (program_id, "order") collisions mid-transaction.
        for topic in topics:
            topic.order = -topic.order
        await self.db.flush()

        for index, topic_id in enumerate(topic_ids, start=1):
            by_id[topic_id].order = index
        await self.db.commit()

        return sorted(topics, key=lambda topic: topic.order)
