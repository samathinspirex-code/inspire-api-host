from datetime import date
from typing import Optional, Union

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
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
    NewsEventReorderRequest,
    NewsEventUpdate,
    PublicNewsEventListResponse,
)
from app.modules.cms import media_service, news_seo


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
    data = payload.model_dump()
    data["image_url"] = await media_service.resolve_media_reference(db, data.get("image_url"))
    program = await repo.create(data)
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

    data = payload.model_dump()
    data["image_url"] = await media_service.resolve_media_reference(db, data.get("image_url"))
    program = await repo.update(program, data)
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
        payment_plans_enabled=bool(getattr(program, "payment_plans_enabled", False)),
        payment_plans=getattr(program, "payment_plans", None) or None,
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


async def _unique_news_slug(repo: NewsEventRepository, base: str, own_id: Optional[int] = None) -> str:
    """Return base, or base-2, base-3 ... when another post already uses it."""
    base = base[:240].strip("-") or "news"
    candidate, number = base, 1
    while True:
        owner = await repo.get_by_slug(candidate)
        if owner is None or owner.news_event_id == own_id:
            return candidate
        number += 1
        candidate = f"{base}-{number}"


async def create_news_event(db: AsyncSession, payload: NewsEventCreate) -> NewsEventItem:
    repo = NewsEventRepository(db)
    data = payload.model_dump()
    # The link, meta title and meta description are written by AI; a failed AI call never blocks saving.
    seo = await news_seo.generate_news_seo(
        payload.title, payload.excerpt, payload.content, kind=payload.kind, category=payload.category
    )
    base_slug = seo["slug"] if seo else news_seo.fallback_slug(payload.title, payload.published_on)
    data["slug"] = await _unique_news_slug(repo, base_slug)
    data["meta_title"] = seo["meta_title"] if seo else None
    data["meta_description"] = seo["meta_description"] if seo else None
    data["image_url"] = await media_service.resolve_media_reference(db, data.get("image_url"))
    data["video_url"] = await media_service.resolve_media_reference(db, data.get("video_url"))
    data["video_poster_url"] = await media_service.resolve_media_reference(db, data.get("video_poster_url"))
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
    data = payload.model_dump()
    # The link never changes on edit, so old URLs and shared links keep working.
    data["slug"] = item.slug
    text_changed = (
        payload.title != item.title
        or payload.excerpt != item.excerpt
        or list(payload.content) != list(item.content or [])
    )
    if text_changed or not item.meta_title or not item.meta_description:
        seo = await news_seo.generate_news_seo(
            payload.title, payload.excerpt, payload.content, kind=payload.kind, category=payload.category
        )
        # When AI is unavailable, clear rather than keep meta that may describe the old text.
        data["meta_title"] = seo["meta_title"] if seo else None
        data["meta_description"] = seo["meta_description"] if seo else None
    data["image_url"] = await media_service.resolve_media_reference(db, data.get("image_url"))
    data["video_url"] = await media_service.resolve_media_reference(db, data.get("video_url"))
    data["video_poster_url"] = await media_service.resolve_media_reference(db, data.get("video_poster_url"))
    if data["status"] == "Published" and data["published_on"] is None:
        data["published_on"] = date.today()
    item = await repo.update(item, data)
    return NewsEventItem.model_validate(item)


async def reorder_news_events(db: AsyncSession, payload: NewsEventReorderRequest) -> None:
    ids = payload.news_event_ids
    if len(ids) != len(set(ids)):
        raise ValidationError("news_event_ids must not contain duplicates")
    repo = NewsEventRepository(db)
    current = await repo.ids_in_website_order()
    missing = set(ids) - set(current)
    if missing:
        raise NotFoundError(f"News or event {sorted(missing)[0]} not found")
    # Posts not in the list keep their current order after the listed ones, so none jump to the top.
    listed = set(ids)
    await repo.reorder(ids + [news_event_id for news_event_id in current if news_event_id not in listed])


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

