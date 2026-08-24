from typing import Optional, Union

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.auth.dependencies import require_access
from app.modules.auth.schemas import CurrentUser
from app.modules.cms import media_service, service
from app.modules.lms import assistant_service
from app.modules.lms.schemas import (
    CourseAssistantAdminResponse,
    CourseAssistantCatalogResponse,
    CourseAssistantIngestionResponse,
    CourseAssistantSystemSettingsResponse,
    CourseAssistantSystemSettingsUpdate,
    CourseKnowledgeSourceResponse,
    CourseKnowledgeSourceUpdate,
    LectureQuestionGenerateRequest,
    LectureQuestionListResponse,
    LectureQuestionResponse,
    LectureQuestionUpsert,
)
from app.modules.cms.schemas import (
    OutcomeCreate,
    OutcomeItem,
    OutcomeListResponse,
    OutcomeReorderRequest,
    ProgramCreate,
    ProgramDetail,
    ProgramDetailWithTopicsOutcomes,
    ProgramListResponse,
    ProgramUpdate,
    TopicCreate,
    TopicItem,
    TopicListResponse,
    TopicReorderRequest,
    NewsEventCreate,
    NewsEventItem,
    NewsEventListResponse,
    NewsEventUpdate,
    MediaAssetListResponse,
    MediaAssetResponse,
    MediaAssetUpdate,
    MediaUploadRequest,
    MediaUploadTicket,
)

router = APIRouter(prefix="/api/v1/cms", tags=["cms"], dependencies=[Depends(require_access("CMS"))])


@router.get("/media", response_model=MediaAssetListResponse)
async def list_media_assets(
    kind: str | None = Query(None), db: AsyncSession = Depends(get_db)
) -> MediaAssetListResponse:
    return await media_service.list_assets(db, kind)


@router.post("/media/uploads", response_model=MediaUploadTicket, status_code=201)
async def request_media_upload(
    payload: MediaUploadRequest,
    current_user: CurrentUser = Depends(require_access("CMS")),
    db: AsyncSession = Depends(get_db),
) -> MediaUploadTicket:
    return await media_service.request_upload(db, payload, current_user.user_id)


@router.get("/media/by-name/{name}", response_model=MediaAssetResponse)
async def get_media_asset_by_name(name: str, db: AsyncSession = Depends(get_db)) -> MediaAssetResponse:
    return await media_service.get_asset_by_name(db, name)


@router.post("/media/{asset_id}/complete", response_model=MediaAssetResponse)
async def complete_media_upload(
    asset_id: int, db: AsyncSession = Depends(get_db)
) -> MediaAssetResponse:
    return await media_service.complete_upload(db, asset_id)


@router.patch("/media/{asset_id}", response_model=MediaAssetResponse)
async def update_media_asset(
    asset_id: int, payload: MediaAssetUpdate, db: AsyncSession = Depends(get_db)
) -> MediaAssetResponse:
    return await media_service.update_asset(db, asset_id, payload)


@router.delete("/media/{asset_id}", status_code=204)
async def delete_media_asset(asset_id: int, db: AsyncSession = Depends(get_db)) -> None:
    await media_service.delete_asset(db, asset_id)


@router.get("/programs", response_model=ProgramListResponse)
async def list_programs(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> ProgramListResponse:
    return await service.list_programs(db, page, size, search)


@router.post("/programs", response_model=ProgramDetail, status_code=201)
async def create_program(payload: ProgramCreate, db: AsyncSession = Depends(get_db)) -> ProgramDetail:
    return await service.create_program(db, payload)


@router.get("/programs/{program_id}", response_model=Union[ProgramDetailWithTopicsOutcomes, ProgramDetail])
async def get_program(
    program_id: int,
    include: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> Union[ProgramDetailWithTopicsOutcomes, ProgramDetail]:
    return await service.get_program(db, program_id, include_topics_outcomes=include == "topics_outcomes")


@router.put("/programs/{program_id}", response_model=ProgramDetail)
async def update_program(
    program_id: int, payload: ProgramUpdate, db: AsyncSession = Depends(get_db)
) -> ProgramDetail:
    return await service.update_program(db, program_id, payload)


@router.delete("/programs/{program_id}", status_code=204)
async def delete_program(program_id: int, db: AsyncSession = Depends(get_db)) -> None:
    await service.delete_program(db, program_id)


@router.get("/programs/{program_id}/topics", response_model=TopicListResponse)
async def list_topics(program_id: int, db: AsyncSession = Depends(get_db)) -> TopicListResponse:
    return await service.list_topics(db, program_id)


@router.post("/programs/{program_id}/topics", response_model=TopicItem, status_code=201)
async def create_topic(
    program_id: int, payload: TopicCreate, db: AsyncSession = Depends(get_db)
) -> TopicItem:
    return await service.create_topic(db, program_id, payload)


@router.put("/topics/{topic_id}", response_model=TopicItem)
async def update_topic(topic_id: int, payload: TopicCreate, db: AsyncSession = Depends(get_db)) -> TopicItem:
    return await service.update_topic(db, topic_id, payload)


@router.delete("/topics/{topic_id}", status_code=204)
async def delete_topic(topic_id: int, db: AsyncSession = Depends(get_db)) -> None:
    await service.delete_topic(db, topic_id)


@router.put("/programs/{program_id}/topics/reorder", response_model=TopicListResponse)
async def reorder_topics(
    program_id: int, payload: TopicReorderRequest, db: AsyncSession = Depends(get_db)
) -> TopicListResponse:
    return await service.reorder_topics(db, program_id, payload.topic_ids)


@router.get("/programs/{program_id}/outcomes", response_model=OutcomeListResponse)
async def list_outcomes(program_id: int, db: AsyncSession = Depends(get_db)) -> OutcomeListResponse:
    return await service.list_outcomes(db, program_id)


@router.post("/programs/{program_id}/outcomes", response_model=OutcomeItem, status_code=201)
async def create_outcome(
    program_id: int, payload: OutcomeCreate, db: AsyncSession = Depends(get_db)
) -> OutcomeItem:
    return await service.create_outcome(db, program_id, payload)


@router.put("/outcomes/{outcome_id}", response_model=OutcomeItem)
async def update_outcome(
    outcome_id: int, payload: OutcomeCreate, db: AsyncSession = Depends(get_db)
) -> OutcomeItem:
    return await service.update_outcome(db, outcome_id, payload)


@router.delete("/outcomes/{outcome_id}", status_code=204)
async def delete_outcome(outcome_id: int, db: AsyncSession = Depends(get_db)) -> None:
    await service.delete_outcome(db, outcome_id)


@router.put("/programs/{program_id}/outcomes/reorder", response_model=OutcomeListResponse)
async def reorder_outcomes(
    program_id: int, payload: OutcomeReorderRequest, db: AsyncSession = Depends(get_db)
) -> OutcomeListResponse:
    return await service.reorder_outcomes(db, program_id, payload.outcome_ids)


@router.get("/news-events", response_model=NewsEventListResponse)
async def list_news_events(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None),
    status: Optional[str] = Query(None, pattern="^(Draft|Review|Published)$"),
    kind: Optional[str] = Query(None, pattern="^(News|Event)$"),
    category: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> NewsEventListResponse:
    return await service.list_news_events(db, page, size, search, status, kind, category)


@router.post("/news-events", response_model=NewsEventItem, status_code=201)
async def create_news_event(
    payload: NewsEventCreate, db: AsyncSession = Depends(get_db)
) -> NewsEventItem:
    return await service.create_news_event(db, payload)


@router.get("/news-events/{news_event_id}", response_model=NewsEventItem)
async def get_news_event(news_event_id: int, db: AsyncSession = Depends(get_db)) -> NewsEventItem:
    return await service.get_news_event(db, news_event_id)


@router.put("/news-events/{news_event_id}", response_model=NewsEventItem)
async def update_news_event(
    news_event_id: int, payload: NewsEventUpdate, db: AsyncSession = Depends(get_db)
) -> NewsEventItem:
    return await service.update_news_event(db, news_event_id, payload)


@router.delete("/news-events/{news_event_id}", status_code=204)
async def delete_news_event(news_event_id: int, db: AsyncSession = Depends(get_db)) -> None:
    await service.delete_news_event(db, news_event_id)


@router.get("/course-assistants", response_model=CourseAssistantCatalogResponse)
async def list_course_assistants(db: AsyncSession = Depends(get_db)) -> CourseAssistantCatalogResponse:
    return await assistant_service.list_admin_courses(db)


@router.get(
    "/course-assistant-config",
    response_model=CourseAssistantSystemSettingsResponse,
)
async def get_course_assistant_config(
    db: AsyncSession = Depends(get_db),
) -> CourseAssistantSystemSettingsResponse:
    return await assistant_service.get_system_settings(db)


@router.put(
    "/course-assistant-config",
    response_model=CourseAssistantSystemSettingsResponse,
)
async def update_course_assistant_config(
    payload: CourseAssistantSystemSettingsUpdate,
    current_user: CurrentUser = Depends(require_access("CMS")),
    db: AsyncSession = Depends(get_db),
) -> CourseAssistantSystemSettingsResponse:
    return await assistant_service.update_system_settings(db, payload, current_user.user_id)


@router.get("/course-assistants/{course_id}", response_model=CourseAssistantAdminResponse)
async def get_course_assistant(course_id: int, db: AsyncSession = Depends(get_db)) -> CourseAssistantAdminResponse:
    return await assistant_service.get_admin_course(db, course_id)


@router.post("/course-assistants/{course_id}/ingest", response_model=CourseAssistantIngestionResponse)
async def ingest_course_assistant_content(
    course_id: int,
    current_user: CurrentUser = Depends(require_access("CMS")),
    db: AsyncSession = Depends(get_db),
) -> CourseAssistantIngestionResponse:
    return await assistant_service.ingest_course_content(db, course_id, current_user.user_id)


@router.put("/course-assistant-sources/{source_id}", response_model=CourseKnowledgeSourceResponse)
async def update_course_assistant_source(
    source_id: int,
    payload: CourseKnowledgeSourceUpdate,
    db: AsyncSession = Depends(get_db),
) -> CourseKnowledgeSourceResponse:
    return await assistant_service.update_source(db, source_id, payload)


@router.get("/course-assistants/{course_id}/questions", response_model=LectureQuestionListResponse)
async def list_course_questions(
    course_id: int, db: AsyncSession = Depends(get_db)
) -> LectureQuestionListResponse:
    return await assistant_service.list_questions(db, course_id)


@router.post("/course-assistants/{course_id}/questions/generate", response_model=LectureQuestionListResponse)
async def generate_course_questions(
    course_id: int,
    payload: LectureQuestionGenerateRequest,
    current_user: CurrentUser = Depends(require_access("CMS")),
    db: AsyncSession = Depends(get_db),
) -> LectureQuestionListResponse:
    return await assistant_service.generate_questions(db, course_id, payload, current_user.user_id)


@router.post("/course-assistants/{course_id}/questions", response_model=LectureQuestionResponse, status_code=201)
async def create_course_question(
    course_id: int,
    payload: LectureQuestionUpsert,
    current_user: CurrentUser = Depends(require_access("CMS")),
    db: AsyncSession = Depends(get_db),
) -> LectureQuestionResponse:
    return await assistant_service.create_question(db, course_id, payload, current_user.user_id)


@router.put("/course-question-bank/{question_id}", response_model=LectureQuestionResponse)
async def update_course_question(
    question_id: int,
    payload: LectureQuestionUpsert,
    db: AsyncSession = Depends(get_db),
) -> LectureQuestionResponse:
    return await assistant_service.update_question(db, question_id, payload)


@router.delete("/course-question-bank/{question_id}", status_code=204)
async def delete_course_question(question_id: int, db: AsyncSession = Depends(get_db)) -> None:
    await assistant_service.delete_question(db, question_id)
