import asyncio
from collections import Counter
from datetime import datetime, timezone
from io import BytesIO
import ipaddress
import re
import socket
from urllib.parse import urljoin, urlparse

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.modules.cms.models import Program
from app.modules.lms import content_service
from app.modules.lms.models import (
    LmsCourse,
    LmsCourseAssistantSettings,
    LmsCourseKnowledgeChunk,
    LmsCourseKnowledgeSource,
    LmsLearningItem,
    LmsModule,
)
from app.modules.lms.schemas import (
    CourseAssistantAdminResponse,
    CourseAssistantAnswer,
    CourseAssistantCatalogItem,
    CourseAssistantCatalogResponse,
    CourseAssistantCitation,
    CourseAssistantIngestionFailure,
    CourseAssistantIngestionResponse,
    CourseAssistantPublicResponse,
    CourseAssistantSettingsResponse,
    CourseAssistantSettingsUpdate,
    CourseKnowledgeSourceCreate,
    CourseKnowledgeSourceResponse,
    CourseKnowledgeSourceUpdate,
)

DEFAULT_WELCOME = "Hi! What would you like to understand about this course?"
DEFAULT_FALLBACK = "I couldn't find that in the approved course resources yet. Try asking about a lesson, topic, or definition."
STOP_WORDS = {
    "a", "about", "an", "and", "are", "can", "could", "do", "for", "from", "give", "how", "i", "in", "is",
    "it", "me", "of", "on", "please", "tell", "the", "this", "to", "what", "with", "would", "you",
}
SUMMARY_WORDS = {"all", "content", "course", "explain", "material", "overview", "summarize", "summary"}
MAX_CHUNK_CHARS = 1400
CHUNK_OVERLAP_CHARS = 180


def _settings(course_id: int, row=None) -> CourseAssistantSettingsResponse:
    return CourseAssistantSettingsResponse(
        course_id=course_id,
        is_enabled=row.is_enabled if row else False,
        assistant_name=row.assistant_name if row else "Course Assistant",
        welcome_message=row.welcome_message if row else DEFAULT_WELCOME,
        fallback_message=row.fallback_message if row else DEFAULT_FALLBACK,
        attention_animation=row.attention_animation if row else True,
    )


async def list_admin_courses(db: AsyncSession) -> CourseAssistantCatalogResponse:
    count = func.count(LmsCourseKnowledgeSource.knowledge_source_id).filter(
        LmsCourseKnowledgeSource.is_approved.is_(True)
    ).label("source_count")
    stmt = (
        select(LmsCourse, Program.title, LmsCourseAssistantSettings.is_enabled, count)
        .join(Program, Program.program_id == LmsCourse.program_id)
        .outerjoin(LmsCourseAssistantSettings, LmsCourseAssistantSettings.course_id == LmsCourse.course_id)
        .outerjoin(LmsCourseKnowledgeSource, LmsCourseKnowledgeSource.course_id == LmsCourse.course_id)
        .group_by(LmsCourse.course_id, Program.title, LmsCourseAssistantSettings.is_enabled)
        .order_by(LmsCourse.title)
    )
    rows = (await db.execute(stmt)).all()
    return CourseAssistantCatalogResponse(data=[CourseAssistantCatalogItem(
        course_id=course.course_id, course_code=course.code, course_title=course.title,
        program_title=program_title, is_enabled=bool(enabled), source_count=source_count,
    ) for course, program_title, enabled, source_count in rows])


async def _source_response(db: AsyncSession, source) -> CourseKnowledgeSourceResponse:
    count = (await db.execute(select(func.count()).where(
        LmsCourseKnowledgeChunk.knowledge_source_id == source.knowledge_source_id
    ))).scalar_one()
    data = CourseKnowledgeSourceResponse.model_validate(source)
    return data.model_copy(update={"chunk_count": count})


async def get_admin_course(db: AsyncSession, course_id: int) -> CourseAssistantAdminResponse:
    stmt = select(LmsCourse, Program.title).join(Program).where(LmsCourse.course_id == course_id)
    row = (await db.execute(stmt)).one_or_none()
    if row is None:
        raise NotFoundError(f"Course {course_id} not found")
    course, program_title = row
    settings_row = await db.get(LmsCourseAssistantSettings, course_id)
    sources = list((await db.execute(
        select(LmsCourseKnowledgeSource).where(LmsCourseKnowledgeSource.course_id == course_id)
        .order_by(LmsCourseKnowledgeSource.title, LmsCourseKnowledgeSource.knowledge_source_id)
    )).scalars().all())
    return CourseAssistantAdminResponse(
        course_id=course_id, course_code=course.code, course_title=course.title,
        program_title=program_title, settings=_settings(course_id, settings_row),
        sources=[await _source_response(db, source) for source in sources],
    )


async def update_settings(db: AsyncSession, course_id: int, payload: CourseAssistantSettingsUpdate, user_id: int):
    if await db.get(LmsCourse, course_id) is None:
        raise NotFoundError(f"Course {course_id} not found")
    row = await db.get(LmsCourseAssistantSettings, course_id)
    data = payload.model_dump()
    data.update(
        assistant_name=payload.assistant_name.strip(), welcome_message=payload.welcome_message.strip(),
        fallback_message=payload.fallback_message.strip(),
    )
    if row is None:
        row = LmsCourseAssistantSettings(course_id=course_id, created_by=user_id, **data)
        db.add(row)
    else:
        for key, value in data.items():
            setattr(row, key, value)
    await db.commit()
    await db.refresh(row)
    return _settings(course_id, row)


async def _validate_learning_item(db: AsyncSession, course_id: int, item_id: int | None):
    if item_id is None:
        return
    stmt = select(LmsLearningItem).join(LmsModule).where(
        LmsLearningItem.learning_item_id == item_id, LmsModule.course_id == course_id
    )
    if (await db.execute(stmt)).scalar_one_or_none() is None:
        raise ValidationError("The selected learning item does not belong to this course")


def chunk_text(text: str, max_chars: int = MAX_CHUNK_CHARS, overlap: int = CHUNK_OVERLAP_CHARS) -> list[str]:
    clean = re.sub(r"[ \t]+", " ", text.replace("\r", "\n"))
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    if not clean:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(clean):
        end = min(len(clean), start + max_chars)
        if end < len(clean):
            candidates = [clean.rfind("\n\n", start, end), clean.rfind(". ", start, end)]
            boundary = max(candidates)
            if boundary > start + max_chars // 2:
                end = boundary + (1 if clean[boundary:boundary + 2] == ". " else 0)
        piece = clean[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(clean):
            break
        start = max(start + 1, end - overlap)
    return chunks


async def _replace_chunks(db: AsyncSession, source, chunks: list[tuple[str, int | None, int | None, int | None]]):
    await db.execute(delete(LmsCourseKnowledgeChunk).where(
        LmsCourseKnowledgeChunk.knowledge_source_id == source.knowledge_source_id
    ))
    for position, (content, page, start, end) in enumerate(chunks, 1):
        db.add(LmsCourseKnowledgeChunk(
            knowledge_source_id=source.knowledge_source_id, content=content, position=position,
            page_number=page, start_seconds=start, end_seconds=end,
        ))
    source.indexed_at = datetime.now(timezone.utc)


async def create_source(db: AsyncSession, course_id: int, payload: CourseKnowledgeSourceCreate, user_id: int):
    if await db.get(LmsCourse, course_id) is None:
        raise NotFoundError(f"Course {course_id} not found")
    await _validate_learning_item(db, course_id, payload.learning_item_id)
    source = LmsCourseKnowledgeSource(
        course_id=course_id, created_by=user_id, ingestion_status="manual",
        **{**payload.model_dump(), "title": payload.title.strip(), "content": payload.content.strip()}
    )
    db.add(source)
    await db.flush()
    pieces = chunk_text(source.content)
    await _replace_chunks(db, source, [(piece, source.page_number, source.start_seconds, source.end_seconds) for piece in pieces])
    await db.commit()
    await db.refresh(source)
    return await _source_response(db, source)


async def update_source(db: AsyncSession, source_id: int, payload: CourseKnowledgeSourceUpdate):
    source = await db.get(LmsCourseKnowledgeSource, source_id)
    if source is None:
        raise NotFoundError(f"Knowledge source {source_id} not found")
    await _validate_learning_item(db, source.course_id, payload.learning_item_id)
    data = {**payload.model_dump(), "title": payload.title.strip(), "content": payload.content.strip()}
    for key, value in data.items():
        setattr(source, key, value)
    source.ingestion_status = "manual"
    pieces = chunk_text(source.content)
    await _replace_chunks(db, source, [(piece, source.page_number, source.start_seconds, source.end_seconds) for piece in pieces])
    await db.commit()
    await db.refresh(source)
    return await _source_response(db, source)


async def delete_source(db: AsyncSession, source_id: int) -> None:
    source = await db.get(LmsCourseKnowledgeSource, source_id)
    if source is None:
        raise NotFoundError(f"Knowledge source {source_id} not found")
    await db.delete(source)
    await db.commit()


async def _validate_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValidationError("PDF resource must use a public HTTP or HTTPS URL")
    if parsed.username or parsed.password:
        raise ValidationError("PDF URLs with embedded credentials are not supported")
    try:
        addresses = await asyncio.to_thread(socket.getaddrinfo, parsed.hostname, parsed.port or 443)
    except socket.gaierror as exc:
        raise ValidationError("PDF host could not be resolved") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise ValidationError("PDF URL must not resolve to a private or local network address")


async def _download_pdf(url: str) -> bytes:
    limit = settings.COURSE_ASSISTANT_MAX_PDF_MB * 1024 * 1024
    current = url
    async with httpx.AsyncClient(timeout=45, follow_redirects=False) as client:
        for _ in range(4):
            await _validate_public_url(current)
            async with client.stream("GET", current, headers={"Accept": "application/pdf"}) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise ValidationError("PDF download redirected without a destination")
                    current = urljoin(current, location)
                    continue
                response.raise_for_status()
                declared = int(response.headers.get("content-length", "0") or 0)
                if declared > limit:
                    raise ValidationError(f"PDF is larger than {settings.COURSE_ASSISTANT_MAX_PDF_MB} MB")
                data = bytearray()
                async for block in response.aiter_bytes():
                    data.extend(block)
                    if len(data) > limit:
                        raise ValidationError(f"PDF is larger than {settings.COURSE_ASSISTANT_MAX_PDF_MB} MB")
                if not bytes(data[:5]).startswith(b"%PDF-"):
                    raise ValidationError("The resource did not return a valid PDF file")
                return bytes(data)
    raise ValidationError("PDF download redirected too many times")


def _extract_pdf_pages(data: bytes) -> list[tuple[int, str]]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ValidationError("PDF extraction dependency is not installed on the API server") from exc
    try:
        reader = PdfReader(BytesIO(data))
        pages = [(index, page.extract_text() or "") for index, page in enumerate(reader.pages, 1)]
    except Exception as exc:
        raise ValidationError("The PDF could not be read or may be encrypted") from exc
    return [(page, text) for page, text in pages if text.strip()]


async def _synced_source(db: AsyncSession, course_id: int, item, source_type: str, user_id: int):
    sync_key = f"learning-item:{item.learning_item_id}"
    source = (await db.execute(select(LmsCourseKnowledgeSource).where(
        LmsCourseKnowledgeSource.course_id == course_id,
        LmsCourseKnowledgeSource.sync_key == sync_key,
    ))).scalar_one_or_none()
    if source is None:
        source = LmsCourseKnowledgeSource(
            course_id=course_id, learning_item_id=item.learning_item_id, source_type=source_type,
            title=item.title, content="Indexing course content…", source_url=item.resource_url,
            is_approved=True, sync_key=sync_key, ingestion_status="indexing", created_by=user_id,
        )
        db.add(source)
        await db.flush()
    else:
        source.title = item.title
        source.learning_item_id = item.learning_item_id
        source.source_type = source_type
        source.source_url = item.resource_url
        source.ingestion_status = "indexing"
    return source


async def ingest_course_content(db: AsyncSession, course_id: int, user_id: int) -> CourseAssistantIngestionResponse:
    if await db.get(LmsCourse, course_id) is None:
        raise NotFoundError(f"Course {course_id} not found")
    items = list((await db.execute(
        select(LmsLearningItem).join(LmsModule).where(
            LmsModule.course_id == course_id,
            LmsLearningItem.item_type.in_(["pdf", "text"]),
        ).order_by(LmsModule.position, LmsLearningItem.position)
    )).scalars().all())
    indexed = 0
    chunk_total = 0
    failures: list[CourseAssistantIngestionFailure] = []
    for item in items:
        source = None
        try:
            source_type = "pdf" if item.item_type == "pdf" else "text_lesson"
            source = await _synced_source(db, course_id, item, source_type, user_id)
            if item.item_type == "text":
                chunks = [(piece, None, None, None) for piece in chunk_text(item.text_content or "")]
                if not chunks:
                    raise ValidationError("Text lesson has no readable content")
                source.content = (item.text_content or "")[:50000]
            else:
                if not item.resource_url:
                    raise ValidationError("PDF learning item has no resource URL")
                pdf_data = await _download_pdf(item.resource_url)
                pages = await asyncio.to_thread(_extract_pdf_pages, pdf_data)
                chunks = [
                    (piece, page_number, None, None)
                    for page_number, page_text in pages
                    for piece in chunk_text(page_text)
                ]
                if not chunks:
                    raise ValidationError("No selectable text was found; this PDF may need OCR")
                source.content = " ".join(content for content, _, _, _ in chunks)[:50000]
            await _replace_chunks(db, source, chunks)
            source.ingestion_status = "indexed"
            await db.commit()
            indexed += 1
            chunk_total += len(chunks)
        except Exception as exc:
            await db.rollback()
            if source and source.knowledge_source_id:
                failed_source = await db.get(LmsCourseKnowledgeSource, source.knowledge_source_id)
                if failed_source:
                    failed_source.ingestion_status = "failed"
                    await db.commit()
            reason = exc.message if isinstance(exc, ValidationError) else str(exc) or "Indexing failed"
            failures.append(CourseAssistantIngestionFailure(
                learning_item_id=item.learning_item_id, title=item.title, reason=reason,
            ))
    return CourseAssistantIngestionResponse(
        course_id=course_id, items_scanned=len(items), sources_indexed=indexed,
        chunks_created=chunk_total, failures=failures,
    )


async def get_public_settings(db: AsyncSession, course_id: int, user_id: int, role: str):
    await content_service._ensure_course_access(db, course_id, user_id, role)
    row = await db.get(LmsCourseAssistantSettings, course_id)
    assistant_settings = _settings(course_id, row)
    suggestions = list((await db.execute(
        select(LmsCourseKnowledgeSource.title).where(
            LmsCourseKnowledgeSource.course_id == course_id,
            LmsCourseKnowledgeSource.is_approved.is_(True),
            LmsCourseKnowledgeSource.ingestion_status.in_(["manual", "indexed"]),
        ).order_by(LmsCourseKnowledgeSource.knowledge_source_id).limit(3)
    )).scalars().all()) if assistant_settings.is_enabled else []
    return CourseAssistantPublicResponse(
        course_id=course_id, is_enabled=assistant_settings.is_enabled,
        assistant_name=assistant_settings.assistant_name,
        welcome_message=assistant_settings.welcome_message,
        attention_animation=assistant_settings.attention_animation,
        suggested_questions=[f"Tell me about {title}" for title in suggestions],
    )


def _tokens(value: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9]+", value.lower()) if len(token) > 1 and token not in STOP_WORDS]


def _terms(value: str) -> set[str]:
    return set(_tokens(value))


def rank_chunks(question: str, rows: list[tuple[LmsCourseKnowledgeChunk, LmsCourseKnowledgeSource]]) -> list:
    query_tokens = _tokens(question)
    query = set(query_tokens)
    summary_request = bool(query & {"summary", "summarize", "overview"})
    meaningful = query - SUMMARY_WORDS
    ranked = []
    for chunk, source in rows:
        title = _terms(source.title)
        counts = Counter(_tokens(chunk.content))
        overlap = meaningful & set(counts)
        score = sum(min(counts[token], 4) for token in overlap)
        score += len(meaningful & title) * 6
        if len(meaningful) > 1 and " ".join(query_tokens) in chunk.content.lower():
            score += 8
        if summary_request and not meaningful:
            score = max(1, 6 - min(chunk.position, 5))
        if score:
            ranked.append((score, chunk, source))
    ranked.sort(key=lambda item: (-item[0], item[2].knowledge_source_id, item[1].position))
    if summary_request and not meaningful:
        # Give an overview breadth across documents before taking later chunks.
        first_by_source = {}
        for item in ranked:
            first_by_source.setdefault(item[2].knowledge_source_id, item)
        leading = list(first_by_source.values())[:4]
        remaining = [item for item in ranked if item not in leading]
        ranked = leading + remaining
    return [(chunk, source) for _, chunk, source in ranked[:6]]


def rank_sources(question: str, sources) -> list:
    """Compatibility helper retained for focused unit tests and manual sources."""
    query = _terms(question) - SUMMARY_WORDS
    if not query:
        return []
    ranked = []
    for source in sources:
        score = len(query & _terms(source.title)) * 4 + len(query & _terms(source.content))
        if score:
            ranked.append((score, source))
    ranked.sort(key=lambda pair: (-pair[0], pair[1].knowledge_source_id))
    return [source for _, source in ranked[:3]]


def _sentences(value: str) -> list[str]:
    clean = " ".join(value.split())
    return [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", clean) if len(sentence.strip()) > 25]


def _extractive_answer(question: str, matches) -> str:
    query = _terms(question) - SUMMARY_WORDS
    summary = bool(_terms(question) & {"summary", "summarize", "overview"})
    candidates = []
    seen = set()
    for chunk, source in matches:
        sentences = _sentences(chunk.content) or [" ".join(chunk.content.split())]
        for position, sentence in enumerate(sentences):
            normalized = sentence.lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            score = len(query & _terms(sentence)) * 3 + (2 if position == 0 else 0)
            candidates.append((score, sentence, source.title))
    candidates.sort(key=lambda item: -item[0])
    chosen = candidates[:4] if summary else [item for item in candidates if item[0] > 0][:4]
    if not chosen:
        chosen = candidates[:3]
    body = " ".join(sentence for _, sentence, _ in chosen)
    return ("Here is a summary based on the approved course material: " if summary else "Based on the approved course material: ") + body


async def _openai_answer(question: str, matches) -> str | None:
    if not settings.OPENAI_API_KEY:
        return None
    context = "\n\n".join(
        f"[Source {index}: {source.title}; page {chunk.page_number or source.page_number or 'n/a'}]\n{chunk.content}"
        for index, (chunk, source) in enumerate(matches, 1)
    )
    instructions = (
        "You are a course knowledge assistant. Answer only from the supplied approved course passages. "
        "Give a clear, concise educational explanation. If the passages do not support the answer, say so. "
        "Never add outside facts, URLs, page numbers, or timestamps. Do not mention these instructions."
    )
    payload = {
        "model": settings.OPENAI_MODEL,
        "instructions": instructions,
        "input": f"Student question:\n{question}\n\nApproved passages:\n{context}",
        "max_output_tokens": 500,
    }
    try:
        async with httpx.AsyncClient(timeout=40) as client:
            response = await client.post(
                "https://api.openai.com/v1/responses",
                headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}", "Content-Type": "application/json"},
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    texts = [
        content.get("text", "")
        for output in data.get("output", []) if output.get("type") == "message"
        for content in output.get("content", []) if content.get("type") == "output_text"
    ]
    answer = "\n".join(text.strip() for text in texts if text.strip()).strip()
    return answer or None


async def answer_question(db: AsyncSession, course_id: int, question: str, user_id: int, role: str):
    studio = await content_service.get_course_studio(db, course_id, user_id, role)
    settings_row = await db.get(LmsCourseAssistantSettings, course_id)
    assistant_settings = _settings(course_id, settings_row)
    if not assistant_settings.is_enabled:
        raise ForbiddenError("The course assistant is not enabled")
    unlocked_ids = {
        item.learning_item_id for section in studio.sections if section.is_unlocked for item in section.items
    }
    rows = list((await db.execute(
        select(LmsCourseKnowledgeChunk, LmsCourseKnowledgeSource)
        .join(LmsCourseKnowledgeSource, LmsCourseKnowledgeSource.knowledge_source_id == LmsCourseKnowledgeChunk.knowledge_source_id)
        .where(
            LmsCourseKnowledgeSource.course_id == course_id,
            LmsCourseKnowledgeSource.is_approved.is_(True),
            LmsCourseKnowledgeSource.ingestion_status.in_(["manual", "indexed"]),
        )
    )).all())
    visible = [(chunk, source) for chunk, source in rows if source.learning_item_id is None or source.learning_item_id in unlocked_ids]
    matches = rank_chunks(question, visible)
    if not matches:
        return CourseAssistantAnswer(answer=assistant_settings.fallback_message, grounded=False, citations=[])
    generated = await _openai_answer(question, matches)
    answer = generated or _extractive_answer(question, matches)
    citations = []
    used = set()
    for chunk, source in matches:
        locator = (source.knowledge_source_id, chunk.page_number, chunk.start_seconds)
        if locator in used:
            continue
        used.add(locator)
        citations.append(CourseAssistantCitation(
            knowledge_source_id=source.knowledge_source_id, title=source.title,
            source_type=source.source_type, source_url=source.source_url,
            page_number=chunk.page_number or source.page_number,
            start_seconds=chunk.start_seconds if chunk.start_seconds is not None else source.start_seconds,
            end_seconds=chunk.end_seconds if chunk.end_seconds is not None else source.end_seconds,
        ))
    return CourseAssistantAnswer(answer=answer, grounded=True, citations=citations[:4])
