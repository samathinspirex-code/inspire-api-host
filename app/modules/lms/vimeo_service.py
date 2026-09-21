"""Server-side Vimeo course library integration.

The browser only receives a one-time Vimeo tus upload URL.  The account access
token remains on the API server at all times.
"""

from __future__ import annotations

import re
import logging
import asyncio
from urllib.parse import quote

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.modules.cms.models import Program
from app.modules.lms.models import CourseLecturer, LmsCourse, LmsLearningItem, LmsModule
from app.modules.lms.repository import ContentRepository
from app.modules.lms.schemas.content import LearningItemResponse
from app.modules.lms.schemas.vimeo import (
    VimeoCourseLibraryResponse,
    VimeoModuleItem,
    VimeoUploadFinalizeRequest,
    VimeoUploadTicketRequest,
    VimeoUploadTicketResponse,
    VimeoVideoItem,
    VimeoWorkspaceResponse,
)


API_BASE = "https://api.vimeo.com"
VIDEO_URI = re.compile(r"^/videos/(\d+)$")
VIMEO_URL = re.compile(r"vimeo\.com/(?:video/)?(\d+)")
logger = logging.getLogger(__name__)
_thumbnail_refresh_slots = asyncio.Semaphore(2)


class VimeoPermissionError(ValidationError):
    """Vimeo resource belongs to a different account or is outside this token's access."""


def is_configured() -> bool:
    return bool(settings.VIMEO_ACCESS_TOKEN.strip())


def _require_configuration() -> None:
    if not is_configured():
        raise ValidationError(
            "Vimeo is not connected yet. Add VIMEO_ACCESS_TOKEN to the API server environment."
        )


def _api_error(response: httpx.Response, action: str) -> ValidationError:
    # Vimeo errors can include account details. Keep the client-facing error useful but safe.
    if response.status_code in {401, 403}:
        if "organize the video" in action:
            return VimeoPermissionError(
                "Vimeo accepted the upload but cannot place it in the course folder. "
                "Generate an authenticated token with private, create, edit, upload, and interact scopes."
            )
        if "folder" in action:
            return VimeoPermissionError(
                "Vimeo refused permission to manage folders. Generate an authenticated token with "
                "private, create, edit, upload, and interact scopes."
            )
        return VimeoPermissionError(f"Vimeo refused permission to {action}. Check the token scopes.")
    if response.status_code == 429:
        return ValidationError("Vimeo is busy. Please wait a moment and try again.")
    return ValidationError(f"Vimeo could not {action}. Please try again or review the Vimeo app settings.")


class VimeoClient:
    def __init__(self):
        _require_configuration()
        self.client = httpx.AsyncClient(
            base_url=API_BASE,
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={
                "Authorization": f"Bearer {settings.VIMEO_ACCESS_TOKEN}",
                "Accept": "application/vnd.vimeo.*+json;version=3.4",
            },
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        await self.client.aclose()

    async def request(self, method: str, path: str, *, action: str, **kwargs):
        response = await self.client.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise _api_error(response, action)
        return response.json() if response.content else {}

    async def ensure_folder(self, name: str, parent_uri: str | None = None) -> str:
        # Vimeo retains the historic "projects" path.  Subfolders are not
        # listed through a parent /folders URL; they are created in the same
        # collection with parent_folder_uri.
        collection = "/me/projects"
        data = await self.request(
            "GET", collection, action="read the Vimeo folders",
            params={"per_page": 100, "fields": "uri,name,parent_folder"},
        )
        for folder in data.get("data", []):
            folder_parent = ((folder.get("parent_folder") or {}).get("uri"))
            if (
                str(folder.get("name", "")).casefold() == name.casefold()
                and folder.get("uri")
                and folder_parent == parent_uri
            ):
                return str(folder["uri"])
        payload = {"name": name}
        if parent_uri:
            payload["parent_folder_uri"] = parent_uri
        created = await self.request(
            "POST", collection, action=f"create the '{name}' Vimeo folder", json=payload
        )
        uri = created.get("uri")
        if not uri:
            raise ValidationError("Vimeo created a folder without returning its location.")
        return str(uri)

    async def create_upload(self, title: str, description: str | None, file_size: int) -> VimeoUploadTicketResponse:
        payload = {
            "name": title,
            "description": description or None,
            "upload": {"approach": "tus", "size": file_size},
            "privacy": {"view": "unlisted", "embed": "whitelist", "download": False},
        }
        if settings.VIMEO_EMBED_DOMAINS:
            payload["privacy"]["domains"] = settings.VIMEO_EMBED_DOMAINS
        created = await self.request("POST", "/me/videos", action="prepare the Vimeo upload", json=payload)
        upload_link = (created.get("upload") or {}).get("upload_link")
        video_uri = created.get("uri")
        if not upload_link or not video_uri:
            raise ValidationError("Vimeo did not return an upload link. Check Vimeo API upload access.")
        # Vimeo's video-creation payload enables whitelist mode, but the
        # domains themselves must be added through the dedicated endpoint.
        await self.update_video_privacy(str(video_uri))
        return VimeoUploadTicketResponse(video_uri=str(video_uri), upload_link=str(upload_link))

    async def get_video(self, video_uri: str) -> dict:
        return await self.request(
            "GET", video_uri, action="read the uploaded Vimeo video",
            params={"fields": "uri,link,name,description,duration,pictures.active,pictures.type,pictures.sizes,transcode.status,transcript.status"},
        )

    async def update_video(self, video_uri: str, title: str, description: str | None) -> dict:
        return await self.request(
            "PATCH", video_uri, action="update the Vimeo video", json={"name": title, "description": description or None}
        )

    async def update_video_privacy(self, video_uri: str) -> dict:
        """Apply the LMS embed policy to an existing Vimeo video."""
        privacy = {"view": "unlisted", "embed": "whitelist", "download": False}
        result = await self.request(
            "PATCH", video_uri, action="configure the Vimeo video privacy", json={"privacy": privacy}
        )
        for domain in settings.VIMEO_EMBED_DOMAINS:
            await self.request(
                "PUT", f"{video_uri}/privacy/domains/{quote(domain, safe='')}",
                action=f"allow {domain} to embed the Vimeo video",
            )
        return result

    async def add_video_to_folder(self, folder_uri: str, video_uri: str) -> None:
        video_id = VIDEO_URI.match(video_uri)
        if video_id is None:
            raise ValidationError("Invalid Vimeo video reference")
        await self.request(
            "PUT", f"{folder_uri.rstrip('/')}/videos/{video_id.group(1)}",
            action="organize the video in its Vimeo folder",
        )

    async def _delete(self, path: str, action: str) -> None:
        response = await self.client.delete(path)
        # A manually removed Vimeo resource is already in the desired state.
        if response.status_code == 404:
            return
        if response.status_code >= 400:
            raise _api_error(response, action)

    async def delete_video(self, video_uri: str) -> None:
        await self._delete(video_uri, "delete the Vimeo video")

    async def remove_video_from_folder(self, folder_uri: str, video_uri: str) -> None:
        video_id = VIDEO_URI.match(video_uri)
        if video_id is None:
            raise ValidationError("Invalid Vimeo video reference")
        await self._delete(
            f"{folder_uri.rstrip('/')}/videos/{video_id.group(1)}",
            "remove the shared Vimeo video from the deleted course folder",
        )

    async def delete_folder(self, folder_uri: str) -> None:
        await self._delete(folder_uri, "delete the Vimeo folder")

    async def list_folder_video_uris(self, folder_uri: str) -> list[str]:
        paths: list[str] = []
        page = 1
        while True:
            data = await self.request(
                "GET", f"{folder_uri.rstrip('/')}/videos", action="read videos in the Vimeo folder",
                params={"per_page": 100, "page": page, "fields": "uri"},
            )
            paths.extend(str(video["uri"]) for video in data.get("data", []) if video.get("uri"))
            if not (data.get("paging") or {}).get("next"):
                return paths
            page += 1


def _thumbnail_url(video: dict) -> str | None:
    sizes = ((video.get("pictures") or {}).get("sizes") or [])
    candidates = [size for size in sizes if size.get("link")]
    if not candidates:
        return None
    # A medium image is crisp in the Course Studio while avoiding unnecessarily large assets.
    candidates.sort(key=lambda size: abs(int(size.get("width") or 0) - 640))
    return str(candidates[0]["link"])


def video_uri_from_url(url: str | None) -> str | None:
    match = VIMEO_URL.search(url or "")
    return f"/videos/{match.group(1)}" if match else None


async def ensure_course_manager(db: AsyncSession, course_id: int, user_id: int, access: list[str]) -> LmsCourse:
    course = await db.get(LmsCourse, course_id)
    if course is None:
        raise NotFoundError(f"Course {course_id} not found")
    if "SUPER_ADMIN" in access or "ADMIN" in access:
        return course
    if "LECTURER" not in access or await db.get(CourseLecturer, (course_id, user_id)) is None:
        raise ForbiddenError("You are not assigned to manage this course")
    return course


async def _course_programme(db: AsyncSession, course: LmsCourse) -> Program:
    programme = await db.get(Program, course.program_id)
    if programme is None:
        raise NotFoundError("The programme for this course no longer exists")
    return programme


async def ensure_course_workspace(db: AsyncSession, course: LmsCourse) -> str:
    if course.vimeo_folder_uri:
        return course.vimeo_folder_uri
    programme = await _course_programme(db, course)
    async with VimeoClient() as vimeo:
        root = await vimeo.ensure_folder(settings.VIMEO_ROOT_FOLDER.strip() or "INSPIRE COLLEGE")
        programme_folder = await vimeo.ensure_folder(f"{programme.code} · {programme.title}", root)
        course_folder = await vimeo.ensure_folder(f"{course.code} · {course.title}", programme_folder)
    course.vimeo_folder_uri = course_folder
    await db.commit()
    return course_folder


async def ensure_module_workspace(db: AsyncSession, course: LmsCourse, module: LmsModule) -> str:
    if module.vimeo_folder_uri:
        return module.vimeo_folder_uri
    course_folder = await ensure_course_workspace(db, course)
    async with VimeoClient() as vimeo:
        module_folder = await vimeo.ensure_folder(f"{module.position:02d} · {module.title}", course_folder)
    module.vimeo_folder_uri = module_folder
    await db.commit()
    return module_folder


async def initialize_workspace(db: AsyncSession, course_id: int, user_id: int, access: list[str]) -> VimeoWorkspaceResponse:
    course = await ensure_course_manager(db, course_id, user_id, access)
    folder_uri = await ensure_course_workspace(db, course)
    return VimeoWorkspaceResponse(
        configured=True, course_id=course.course_id, folder_uri=folder_uri,
        message="Vimeo course workspace is ready.",
    )


async def get_library(db: AsyncSession, course_id: int, user_id: int, access: list[str]) -> VimeoCourseLibraryResponse:
    course = await ensure_course_manager(db, course_id, user_id, access)
    modules = list((await db.execute(
        select(LmsModule).where(LmsModule.course_id == course.course_id).order_by(LmsModule.position)
    )).scalars().all())
    rows = (await db.execute(
        select(LmsLearningItem, LmsModule.title)
        .join(LmsModule, LmsModule.module_id == LmsLearningItem.module_id)
        .where(LmsModule.course_id == course.course_id, LmsLearningItem.item_type == "video")
        .order_by(LmsModule.position, LmsLearningItem.position)
    )).all()
    videos = [VimeoVideoItem(
        learning_item_id=item.learning_item_id, module_id=item.module_id, module_title=module_title,
        title=item.title, description=item.description, resource_url=item.resource_url or "",
        thumbnail_url=item.thumbnail_url,
        duration_minutes=item.duration_minutes, status=item.status,
    ) for item, module_title in rows if item.resource_url]
    return VimeoCourseLibraryResponse(
        configured=is_configured(), course_id=course.course_id,
        folder_uri=course.vimeo_folder_uri,
        modules=[VimeoModuleItem(module_id=module.module_id, title=module.title, position=module.position) for module in modules],
        videos=videos,
    )


async def create_upload_ticket(
    db: AsyncSession, course_id: int, payload: VimeoUploadTicketRequest, user_id: int, access: list[str]
) -> VimeoUploadTicketResponse:
    course = await ensure_course_manager(db, course_id, user_id, access)
    module = await db.get(LmsModule, payload.module_id)
    if module is None or module.course_id != course.course_id:
        raise ValidationError("Choose a section that belongs to this course")
    if payload.file_size > settings.VIMEO_MAX_UPLOAD_MB * 1024 * 1024:
        raise ValidationError(f"The video is larger than the {settings.VIMEO_MAX_UPLOAD_MB:,} MB LMS upload limit")
    await ensure_module_workspace(db, course, module)
    async with VimeoClient() as vimeo:
        return await vimeo.create_upload(payload.title.strip(), payload.description, payload.file_size)


async def finalize_upload(
    db: AsyncSession, course_id: int, payload: VimeoUploadFinalizeRequest, user_id: int, access: list[str]
) -> LearningItemResponse:
    course = await ensure_course_manager(db, course_id, user_id, access)
    module = await db.get(LmsModule, payload.module_id)
    if module is None or module.course_id != course.course_id:
        raise ValidationError("Choose a section that belongs to this course")
    if VIDEO_URI.match(payload.video_uri) is None:
        raise ValidationError("Invalid Vimeo video reference")
    folder_uri = await ensure_module_workspace(db, course, module)
    async with VimeoClient() as vimeo:
        video = await vimeo.get_video(payload.video_uri)
        await vimeo.add_video_to_folder(folder_uri, payload.video_uri)
    resource_url = str(video.get("link") or "")
    if not resource_url:
        raise ValidationError("Vimeo has not finished preparing this video. Please wait a moment and try again.")
    duplicate = (await db.execute(select(LmsLearningItem.learning_item_id).where(
        LmsLearningItem.module_id == module.module_id,
        LmsLearningItem.resource_url == resource_url,
    ))).scalar_one_or_none()
    if duplicate is not None:
        raise ValidationError("This Vimeo video is already linked to this section")
    item = await ContentRepository(db).create_item({
        "module_id": module.module_id,
        "item_type": "video",
        "title": payload.title.strip(),
        "description": payload.description.strip() if payload.description else None,
        "resource_url": resource_url,
        "thumbnail_url": _thumbnail_url(video),
        "text_content": None,
        "duration_minutes": payload.duration_minutes or max(1, round(int(video.get("duration") or 0) / 60)),
        "position": await ContentRepository(db).next_position(module.module_id),
        "status": payload.status,
        "is_required": payload.is_required,
        "created_by": user_id,
    })
    return LearningItemResponse.model_validate(item)


def _is_ready_thumbnail(url: str | None) -> bool:
    # Vimeo exposes a real image link before transcoding has completed, but it
    # is a generic dark camera placeholder whose URL contains "default". It
    # must remain eligible for later refreshes regardless of its separator.
    return bool(url and "default" not in url.lower())


async def refresh_learning_item_thumbnail(db: AsyncSession, item_id: int) -> bool:
    """Store Vimeo's final thumbnail when it is available for an LMS video."""
    item = await db.get(LmsLearningItem, item_id)
    if item is None or item.item_type != "video":
        return False
    resource_url = item.resource_url
    current_thumbnail = item.thumbnail_url
    video_uri = video_uri_from_url(resource_url)
    if not video_uri:
        return False
    # Release the scarce PostgreSQL session before waiting on Vimeo.
    await db.rollback()
    async with VimeoClient() as vimeo:
        video = await vimeo.get_video(video_uri)
    thumbnail_url = _thumbnail_url(video)
    if not _is_ready_thumbnail(thumbnail_url):
        return False
    if current_thumbnail != thumbnail_url:
        item = await db.get(LmsLearningItem, item_id)
        if item is None or item.item_type != "video" or item.resource_url != resource_url:
            return False
        item.thumbnail_url = thumbnail_url
        await db.commit()
    return True


async def wait_for_learning_item_thumbnail(
    item_id: int, attempts: int = 6, delay_seconds: int = 10
) -> None:
    """Refresh a just-uploaded video without holding up the upload response.

    Vimeo can need several minutes to transcode a larger lecture and create its
    final image. The notification worker below continues any unfinished work.
    """
    from app.core.database import AsyncSessionLocal

    last_error = None
    for attempt in range(attempts):
        try:
            async with _thumbnail_refresh_slots:
                async with AsyncSessionLocal() as db:
                    if await refresh_learning_item_thumbnail(db, item_id):
                        return
            last_error = None
        except Exception as exc:  # A retry is safer than making a successful upload appear failed.
            last_error = exc
        if attempt < attempts - 1:
            await asyncio.sleep(delay_seconds)
    if last_error is not None:
        logger.warning(
            "Could not refresh Vimeo thumbnail for LMS item %s after %s attempts: %s",
            item_id, attempts, last_error,
        )


async def refresh_recent_learning_item_thumbnails(db: AsyncSession, limit: int = 100) -> int:
    """Force a one-time refresh for existing Vimeo videos with stale image URLs."""
    if not is_configured():
        return 0
    rows = await db.execute(
        select(LmsLearningItem.learning_item_id)
        .where(LmsLearningItem.item_type == "video", LmsLearningItem.resource_url.is_not(None))
        .order_by(LmsLearningItem.updated_at.desc())
        .limit(limit)
    )
    refreshed = 0
    for item_id in rows.scalars():
        try:
            refreshed += int(await refresh_learning_item_thumbnail(db, item_id))
        except Exception:
            logger.warning("Could not refresh Vimeo thumbnail for LMS item %s", item_id, exc_info=True)
    return refreshed


async def refresh_pending_learning_item_thumbnails(db: AsyncSession, limit: int = 20) -> int:
    """Pick up video thumbnails that were unavailable when their upload completed."""
    if not is_configured():
        return 0
    rows = await db.execute(
        select(LmsLearningItem.learning_item_id)
        .where(
            LmsLearningItem.item_type == "video",
            LmsLearningItem.resource_url.is_not(None),
            or_(
                LmsLearningItem.thumbnail_url.is_(None),
                LmsLearningItem.thumbnail_url.ilike("%default%"),
            ),
        )
        .order_by(LmsLearningItem.updated_at.desc())
        .limit(limit)
    )
    refreshed = 0
    for item_id in rows.scalars():
        try:
            refreshed += int(await refresh_learning_item_thumbnail(db, item_id))
        except Exception:
            # Leave it pending: Vimeo can still be transcoding or temporarily busy.
            logger.warning("Could not refresh pending Vimeo thumbnail for LMS item %s", item_id, exc_info=True)
    return refreshed


async def delete_learning_item_video(db: AsyncSession, item: LmsLearningItem) -> None:
    """Delete an LMS-owned Vimeo video unless another LMS item still uses it."""
    if item.item_type != "video":
        return
    video_uri = video_uri_from_url(item.resource_url)
    if not video_uri:
        return
    references = (await db.execute(select(
        LmsLearningItem.learning_item_id, LmsLearningItem.resource_url
    ).where(
        LmsLearningItem.learning_item_id != item.learning_item_id,
        LmsLearningItem.item_type == "video",
    ))).all()
    if any(video_uri_from_url(url) == video_uri for _, url in references):
        return
    _require_configuration()
    async with VimeoClient() as vimeo:
        try:
            await vimeo.delete_video(video_uri)
        except VimeoPermissionError:
            # Older LMS records can point at a video owned by another Vimeo
            # account. Do not prevent deletion of the LMS record in that case.
            logger.warning("Vimeo video %s could not be removed because this token does not own it", video_uri)


async def _shared_video_uris_outside_module(db: AsyncSession, module_id: int) -> set[str]:
    rows = (await db.execute(select(LmsLearningItem.resource_url).where(
        LmsLearningItem.module_id != module_id,
        LmsLearningItem.item_type == "video",
    ))).scalars().all()
    return {uri for url in rows if (uri := video_uri_from_url(url))}


async def delete_module_workspace(db: AsyncSession, module: LmsModule) -> None:
    """Remove unshared section videos, including older videos linked before folders existed."""
    _require_configuration()
    shared_uris = await _shared_video_uris_outside_module(db, module.module_id)
    local_uris = {
        uri for url in (await db.execute(select(LmsLearningItem.resource_url).where(
            LmsLearningItem.module_id == module.module_id,
            LmsLearningItem.item_type == "video",
        ))).scalars().all()
        if (uri := video_uri_from_url(url))
    }
    async with VimeoClient() as vimeo:
        folder_uris = (
            await vimeo.list_folder_video_uris(module.vimeo_folder_uri)
            if module.vimeo_folder_uri else []
        )
        for video_uri in local_uris | set(folder_uris):
            if video_uri in shared_uris:
                if module.vimeo_folder_uri and video_uri in folder_uris:
                    try:
                        await vimeo.remove_video_from_folder(module.vimeo_folder_uri, video_uri)
                    except VimeoPermissionError:
                        logger.warning("Vimeo video %s could not be removed from its folder", video_uri)
            else:
                try:
                    await vimeo.delete_video(video_uri)
                except VimeoPermissionError:
                    logger.warning("Vimeo video %s could not be deleted because this token does not own it", video_uri)
        if module.vimeo_folder_uri:
            try:
                await vimeo.delete_folder(module.vimeo_folder_uri)
            except VimeoPermissionError:
                logger.warning("Vimeo folder %s could not be deleted because this token does not own it", module.vimeo_folder_uri)


async def delete_course_workspace(db: AsyncSession, course: LmsCourse) -> None:
    """Cascade a course deletion through its dedicated module and course folders."""
    modules = list((await db.execute(
        select(LmsModule).where(LmsModule.course_id == course.course_id)
    )).scalars().all())
    for module in modules:
        await delete_module_workspace(db, module)
    if course.vimeo_folder_uri:
        _require_configuration()
        async with VimeoClient() as vimeo:
            try:
                await vimeo.delete_folder(course.vimeo_folder_uri)
            except VimeoPermissionError:
                logger.warning("Vimeo folder %s could not be deleted because this token does not own it", course.vimeo_folder_uri)
