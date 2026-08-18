from datetime import date
from typing import Optional, Union

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.modules.cms.repository import (
    OutcomeRepository,
    ProgramRepository,
    TopicRepository,
    NewsEventRepository,
)
from app.modules.cms.schemas import (
    OutcomeCreate,
    OutcomeEmbedded,
    OutcomeItem,
    OutcomeListResponse,
    Pagination,
    ProgramCreate,
    ProgramDetail,
    ProgramDetailWithTopicsOutcomes,
    ProgramListItem,
    ProgramListResponse,
    ProgramUpdate,
    PublicProgramListResponse,
    TopicCreate,
    TopicEmbedded,
    TopicItem,
    TopicListResponse,
    NewsEventCreate,
    NewsEventItem,
    NewsEventListResponse,
    NewsEventUpdate,
    PublicNewsEventListResponse,
)


async def list_programs(
    db: AsyncSession,
    page: int,
    size: int,
    search: Optional[str],
) -> ProgramListResponse:
    repo = ProgramRepository(db)
    total_items = await repo.count(search)
    programs = await repo.list_programs(search, page, size)

    data = [ProgramListItem.model_validate(program) for program in programs]
    total_pages = (total_items + size - 1) // size if total_items else 0

    return ProgramListResponse(
        data=data,
        pagination=Pagination(page=page, size=size, total_items=total_items, total_pages=total_pages),
    )


async def create_program(db: AsyncSession, payload: ProgramCreate) -> ProgramDetail:
    repo = ProgramRepository(db)
    program = await repo.create(payload.model_dump())
    return ProgramDetail.model_validate(program)


async def get_program(
    db: AsyncSession, program_id: int, include_topics_outcomes: bool = False
) -> Union[ProgramDetailWithTopicsOutcomes, ProgramDetail]:
    repo = ProgramRepository(db)
    program = await repo.get(program_id)
    if program is None:
        raise NotFoundError(f"Program {program_id} not found")

    if not include_topics_outcomes:
        return ProgramDetail.model_validate(program)

    topics = await TopicRepository(db).list_by_program(program_id)
    outcomes = await OutcomeRepository(db).list_by_program(program_id)

    return ProgramDetailWithTopicsOutcomes(
        **ProgramDetail.model_validate(program).model_dump(),
        topics=[TopicEmbedded.model_validate(topic) for topic in topics],
        outcomes=[OutcomeEmbedded.model_validate(outcome) for outcome in outcomes],
    )


async def update_program(db: AsyncSession, program_id: int, payload: ProgramUpdate) -> ProgramDetail:
    repo = ProgramRepository(db)
    program = await repo.get(program_id)
    if program is None:
        raise NotFoundError(f"Program {program_id} not found")

    program = await repo.update(program, payload.model_dump())
    return ProgramDetail.model_validate(program)


async def delete_program(db: AsyncSession, program_id: int) -> None:
    repo = ProgramRepository(db)
    program = await repo.get(program_id)
    if program is None:
        raise NotFoundError(f"Program {program_id} not found")

    await repo.delete(program)


async def list_topics(db: AsyncSession, program_id: int) -> TopicListResponse:
    program_repo = ProgramRepository(db)
    program = await program_repo.get(program_id)
    if program is None:
        raise NotFoundError(f"Program {program_id} not found")

    topics = await TopicRepository(db).list_by_program(program_id)
    return TopicListResponse(data=[TopicItem.model_validate(topic) for topic in topics])


async def create_topic(db: AsyncSession, program_id: int, payload: TopicCreate) -> TopicItem:
    program_repo = ProgramRepository(db)
    program = await program_repo.get(program_id)
    if program is None:
        raise NotFoundError(f"Program {program_id} not found")

    topic = await TopicRepository(db).create(program_id, payload.topic)
    return TopicItem.model_validate(topic)


async def update_topic(db: AsyncSession, topic_id: int, payload: TopicCreate) -> TopicItem:
    topic_repo = TopicRepository(db)
    topic = await topic_repo.get(topic_id)
    if topic is None:
        raise NotFoundError(f"Topic {topic_id} not found")

    topic = await topic_repo.update(topic, payload.topic)
    return TopicItem.model_validate(topic)


async def delete_topic(db: AsyncSession, topic_id: int) -> None:
    topic_repo = TopicRepository(db)
    topic = await topic_repo.get(topic_id)
    if topic is None:
        raise NotFoundError(f"Topic {topic_id} not found")

    await topic_repo.delete_and_renumber(topic)


async def reorder_topics(db: AsyncSession, program_id: int, topic_ids: list[int]) -> TopicListResponse:
    program_repo = ProgramRepository(db)
    program = await program_repo.get(program_id)
    if program is None:
        raise NotFoundError(f"Program {program_id} not found")

    topic_repo = TopicRepository(db)
    topics = await topic_repo.list_by_program(program_id)
    existing_ids = {topic.topic_id for topic in topics}

    if len(topic_ids) != len(set(topic_ids)) or set(topic_ids) != existing_ids:
        raise ValidationError(
            "topic_ids must contain exactly the set of topic IDs belonging to the program, with no duplicates"
        )

    reordered = await topic_repo.reorder(topics, topic_ids)
    return TopicListResponse(data=[TopicItem.model_validate(topic) for topic in reordered])


async def list_outcomes(db: AsyncSession, program_id: int) -> OutcomeListResponse:
    program_repo = ProgramRepository(db)
    program = await program_repo.get(program_id)
    if program is None:
        raise NotFoundError(f"Program {program_id} not found")

    outcomes = await OutcomeRepository(db).list_by_program(program_id)
    return OutcomeListResponse(data=[OutcomeItem.model_validate(outcome) for outcome in outcomes])


async def create_outcome(db: AsyncSession, program_id: int, payload: OutcomeCreate) -> OutcomeItem:
    program_repo = ProgramRepository(db)
    program = await program_repo.get(program_id)
    if program is None:
        raise NotFoundError(f"Program {program_id} not found")

    outcome = await OutcomeRepository(db).create(program_id, payload.outcome)
    return OutcomeItem.model_validate(outcome)


async def update_outcome(db: AsyncSession, outcome_id: int, payload: OutcomeCreate) -> OutcomeItem:
    outcome_repo = OutcomeRepository(db)
    outcome = await outcome_repo.get(outcome_id)
    if outcome is None:
        raise NotFoundError(f"Outcome {outcome_id} not found")

    outcome = await outcome_repo.update(outcome, payload.outcome)
    return OutcomeItem.model_validate(outcome)


async def delete_outcome(db: AsyncSession, outcome_id: int) -> None:
    outcome_repo = OutcomeRepository(db)
    outcome = await outcome_repo.get(outcome_id)
    if outcome is None:
        raise NotFoundError(f"Outcome {outcome_id} not found")

    await outcome_repo.delete_and_renumber(outcome)


async def reorder_outcomes(db: AsyncSession, program_id: int, outcome_ids: list[int]) -> OutcomeListResponse:
    program_repo = ProgramRepository(db)
    program = await program_repo.get(program_id)
    if program is None:
        raise NotFoundError(f"Program {program_id} not found")

    outcome_repo = OutcomeRepository(db)
    outcomes = await outcome_repo.list_by_program(program_id)
    existing_ids = {outcome.outcome_id for outcome in outcomes}

    if len(outcome_ids) != len(set(outcome_ids)) or set(outcome_ids) != existing_ids:
        raise ValidationError(
            "outcome_ids must contain exactly the set of outcome IDs belonging to the program, with no duplicates"
        )

    reordered = await outcome_repo.reorder(outcomes, outcome_ids)
    return OutcomeListResponse(data=[OutcomeItem.model_validate(outcome) for outcome in reordered])


# ===== Public (no-auth) service functions =====

async def list_all_programs(db: AsyncSession) -> PublicProgramListResponse:
    repo = ProgramRepository(db)
    programs = await repo.list_all_programs()
    return PublicProgramListResponse(
        data=[ProgramListItem.model_validate(program) for program in programs],
    )


async def get_program_by_slug(db: AsyncSession, slug: str) -> ProgramDetailWithTopicsOutcomes:
    repo = ProgramRepository(db)
    program = await repo.get_by_slug(slug)
    if program is None:
        raise NotFoundError(f"Program with slug '{slug}' not found")

    topics = await TopicRepository(db).list_by_program(program.program_id)
    outcomes = await OutcomeRepository(db).list_by_program(program.program_id)

    return ProgramDetailWithTopicsOutcomes(
        **ProgramDetail.model_validate(program).model_dump(),
        topics=[TopicEmbedded.model_validate(topic) for topic in topics],
        outcomes=[OutcomeEmbedded.model_validate(outcome) for outcome in outcomes],
    )


# ===== News & Events =====

async def list_news_events(
    db: AsyncSession,
    page: int,
    size: int,
    search: Optional[str],
    status: Optional[str],
    kind: Optional[str],
    category: Optional[str],
) -> NewsEventListResponse:
    repo = NewsEventRepository(db)
    filters = {"search": search, "status": status, "kind": kind, "category": category}
    total_items = await repo.count(**filters)
    items = await repo.list(page, size, **filters)
    total_pages = (total_items + size - 1) // size if total_items else 0
    return NewsEventListResponse(
        data=[NewsEventItem.model_validate(item) for item in items],
        pagination=Pagination(page=page, size=size, total_items=total_items, total_pages=total_pages),
    )


async def create_news_event(db: AsyncSession, payload: NewsEventCreate) -> NewsEventItem:
    repo = NewsEventRepository(db)
    if await repo.get_by_slug(payload.slug):
        raise ConflictError(f"News or event slug '{payload.slug}' already exists")
    data = payload.model_dump()
    if data["status"] == "Published" and data["published_on"] is None:
        data["published_on"] = date.today()
    item = await repo.create(data)
    return NewsEventItem.model_validate(item)


async def get_news_event(db: AsyncSession, news_event_id: int) -> NewsEventItem:
    item = await NewsEventRepository(db).get(news_event_id)
    if item is None:
        raise NotFoundError(f"News or event {news_event_id} not found")
    return NewsEventItem.model_validate(item)


async def update_news_event(
    db: AsyncSession, news_event_id: int, payload: NewsEventUpdate
) -> NewsEventItem:
    repo = NewsEventRepository(db)
    item = await repo.get(news_event_id)
    if item is None:
        raise NotFoundError(f"News or event {news_event_id} not found")
    slug_owner = await repo.get_by_slug(payload.slug)
    if slug_owner is not None and slug_owner.news_event_id != news_event_id:
        raise ConflictError(f"News or event slug '{payload.slug}' already exists")
    data = payload.model_dump()
    if data["status"] == "Published" and data["published_on"] is None:
        data["published_on"] = date.today()
    item = await repo.update(item, data)
    return NewsEventItem.model_validate(item)


async def delete_news_event(db: AsyncSession, news_event_id: int) -> None:
    repo = NewsEventRepository(db)
    item = await repo.get(news_event_id)
    if item is None:
        raise NotFoundError(f"News or event {news_event_id} not found")
    await repo.delete(item)


async def list_public_news_events(
    db: AsyncSession, limit: int, kind: Optional[str], category: Optional[str]
) -> PublicNewsEventListResponse:
    items = await NewsEventRepository(db).list_published(limit, kind, category)
    return PublicNewsEventListResponse(data=[NewsEventItem.model_validate(item) for item in items])


async def get_public_news_event(db: AsyncSession, slug: str) -> NewsEventItem:
    item = await NewsEventRepository(db).get_published_by_slug(slug)
    if item is None:
        raise NotFoundError(f"Published news or event with slug '{slug}' not found")
    return NewsEventItem.model_validate(item)

