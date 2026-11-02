"""Super-admin visibility and guarded Zoom cloud recording cleanup."""
from datetime import datetime, timedelta, timezone
import re

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.modules.lms import vimeo_service, zoom_service


def _storage_bytes(value) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    match = re.fullmatch(r"\s*([\d.]+)\s*(B|KB|MB|GB|TB)?\s*", str(value or ""), re.I)
    if not match:
        return 0
    return int(float(match.group(1)) * 1024 ** {"B": 0, "KB": 1, "MB": 2, "GB": 3, "TB": 4}[match.group(2).upper() if match.group(2) else "B"])


async def _storage(db: AsyncSession) -> dict:
    host = (await db.execute(text("SELECT * FROM lms_zoom_host_connections WHERE enabled=TRUE ORDER BY connection_id LIMIT 1"))).mappings().first()
    if not host:
        return {"available": False, "message": "Connect an active Zoom host to view cloud storage."}
    try:
        token = await zoom_service._access_token(db, dict(host))
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(f"{zoom_service.ZOOM_API}/accounts/me/plans/usage", headers={"Authorization": f"Bearer {token}"})
        if response.is_error:
            if response.status_code == 400 and response.json().get("code") == 4711:
                return {"available": False, "message": "Zoom storage access needs the billing:read:plan_usage:admin permission. Add it to the Zoom app and reconnect the host accounts."}
            return {"available": False, "message": f"Zoom storage is unavailable ({zoom_service._zoom_error(response)})."}
        recording = response.json().get("plan_recording") or {}
        if isinstance(recording, list):
            recording = recording[0] if recording else {}
        total = _storage_bytes(recording.get("free_storage")) + _storage_bytes(recording.get("plan_storage"))
        used = _storage_bytes(recording.get("free_storage_usage")) + _storage_bytes(recording.get("plan_storage_usage"))
        if total <= 0:
            return {"available": False, "message": "Zoom did not return a recording storage allowance for this account."}
        return {"available": True, "total_bytes": total, "used_bytes": used, "remaining_bytes": max(0, total - used), "percent_used": min(100, round(used * 100 / total, 1))}
    except Exception:
        return {"available": False, "message": "Zoom storage could not be read right now. Try refreshing this page."}


async def _visible_in_class(db: AsyncSession, row: dict) -> bool:
    if not row.get("class_id") or not row.get("resource_url"):
        return False
    # The class recording screen only shows the preferred video layout for a meeting.
    rows = (await db.execute(text("""SELECT recording_id,recording_type FROM lms_zoom_recordings
      WHERE meeting_id=:meeting_id AND status='published' AND resource_url IS NOT NULL"""), {"meeting_id": row["meeting_id"]})).mappings().all()
    if not rows:
        return False
    preferred = min((str(item["recording_type"] or "") for item in rows), key=zoom_service._recording_type_priority)
    return any(item["recording_id"] == row["recording_id"] and str(item["recording_type"] or "") == preferred for item in rows)


async def ensure_safe_to_delete(db: AsyncSession, row: dict) -> None:
    if row.get("status") != "published" or not row.get("zoom_published_at") or not row.get("vimeo_video_uri") or not row.get("resource_url"):
        raise ValidationError("This recording is still transferring or processing. Zoom deletion is blocked until the Vimeo copy is published and viewable in its class.")
    if not await _visible_in_class(db, row):
        raise ValidationError("This recording is not viewable in its class Recordings section. Zoom deletion is blocked.")
    try:
        async with vimeo_service.VimeoClient() as vimeo:
            video = await vimeo.get_video(row["vimeo_video_uri"])
    except Exception as exc:
        raise ValidationError("The Vimeo copy could not be verified. Zoom deletion is blocked; retry after Vimeo is available.") from exc
    if not video.get("link") or (video.get("transcode") or {}).get("status") != "complete":
        raise ValidationError("The Vimeo video is still processing or is not playable. Zoom deletion is blocked.")


async def delete_zoom_file(db: AsyncSession, row: dict) -> None:
    host = (await db.execute(text("SELECT * FROM lms_zoom_host_connections WHERE connection_id=:id"), {"id": row["zoom_host_connection_id"]})).mappings().first()
    if not host:
        raise ValidationError("The Zoom host for this recording must be reconnected.")
    token = await zoom_service._access_token(db, dict(host))
    meeting_ref = zoom_service._recording_api_ref(row["provider_meeting_uuid"] or row["provider_meeting_id"])
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.delete(f"{zoom_service.ZOOM_API}/meetings/{meeting_ref}/recordings/{row['zoom_recording_file_id']}", params={"action": "delete"}, headers={"Authorization": f"Bearer {token}"})
    # A missing file is success only when Zoom identifies it as a missing recording.
    already_gone = response.status_code == 404 and response.json().get("code") == 3301
    if response.is_error and not already_gone:
        raise ValidationError(f"Zoom refused to delete this recording ({zoom_service._zoom_error(response)}).")
    await db.execute(text("""UPDATE lms_zoom_recordings SET zoom_cloud_deleted_at=now(),
      zoom_cloud_delete_error=NULL,updated_at=now() WHERE recording_id=:id"""), {"id": row["recording_id"]})
    await db.commit()


async def dashboard(db: AsyncSession) -> dict:
    retention = int(await db.scalar(text("SELECT cloud_retention_days FROM lms_zoom_settings WHERE settings_id=1")) or 2)
    rows = (await db.execute(text("""SELECT zr.recording_id,zr.meeting_id,zr.title,zr.status,zr.recording_type,
      zr.vimeo_video_uri,zr.resource_url,zr.zoom_published_at,zr.zoom_cloud_deleted_at,zr.zoom_cloud_delete_error,
      m.class_id,c.name AS class_name FROM lms_zoom_recordings zr
      JOIN lms_online_meetings m ON m.meeting_id=zr.meeting_id
      LEFT JOIN lms_classes c ON c.class_id=m.class_id
      ORDER BY zr.zoom_published_at DESC NULLS LAST"""))).mappings().all()
    now = datetime.now(timezone.utc)
    waiting = []
    recent = []
    preferred_by_meeting = {}
    for item in rows:
        if item["status"] == "published" and item["resource_url"]:
            meeting_id = item["meeting_id"]
            candidate = str(item["recording_type"] or "")
            if meeting_id not in preferred_by_meeting or zoom_service._recording_type_priority(candidate) < zoom_service._recording_type_priority(preferred_by_meeting[meeting_id]):
                preferred_by_meeting[meeting_id] = candidate
    for raw in rows:
        row = dict(raw)
        published = row["zoom_published_at"]
        row["cleanup_at"] = published + timedelta(days=retention) if published else None
        row["visible_in_class"] = bool(row["class_id"] and row["status"] == "published" and row["resource_url"] and str(row["recording_type"] or "") == preferred_by_meeting.get(row["meeting_id"]))
        if row["status"] == "published" and not row["zoom_cloud_deleted_at"]:
            waiting.append(row)
        elif row["zoom_cloud_deleted_at"]:
            recent.append(row)
    waiting.sort(key=lambda item: item["cleanup_at"] or datetime.max.replace(tzinfo=timezone.utc))
    jobs = (await db.execute(text("""SELECT status,count(*) AS total FROM lms_zoom_jobs
      WHERE job_type='recording' AND status IN ('pending','processing','failed') GROUP BY status"""))).mappings().all()
    return {"retention_days": retention, "counts": {"waiting": len(waiting), "due": sum(bool(item["cleanup_at"] and item["cleanup_at"] <= now) for item in waiting), "deleted": len(recent), "processing_jobs": sum(item["total"] for item in jobs if item["status"] in {"pending", "processing"}), "failed_jobs": sum(item["total"] for item in jobs if item["status"] == "failed")}, "waiting": waiting[:100], "recent": recent[:20], "storage": await _storage(db)}


async def delete_early(db: AsyncSession, recording_id: int, confirmed: bool) -> dict:
    if not confirmed:
        raise ValidationError("Confirm that you want to permanently remove this Zoom copy.")
    row = (await db.execute(text("""SELECT zr.*,m.class_id,m.provider_meeting_uuid,m.provider_meeting_id,m.zoom_host_connection_id
      FROM lms_zoom_recordings zr JOIN lms_online_meetings m ON m.meeting_id=zr.meeting_id
      WHERE zr.recording_id=:id FOR UPDATE OF zr"""), {"id": recording_id})).mappings().first()
    if not row:
        raise NotFoundError("Recording not found")
    row = dict(row)
    if row["zoom_cloud_deleted_at"]:
        raise ValidationError("This Zoom recording has already been deleted.")
    await ensure_safe_to_delete(db, row)
    await delete_zoom_file(db, row)
    return {"recording_id": recording_id, "deleted": True}


async def check_early_delete(db: AsyncSession, recording_id: int) -> dict:
    row = (await db.execute(text("""SELECT zr.*,m.class_id FROM lms_zoom_recordings zr
      JOIN lms_online_meetings m ON m.meeting_id=zr.meeting_id
      WHERE zr.recording_id=:id"""), {"id": recording_id})).mappings().first()
    if not row:
        raise NotFoundError("Recording not found or still transferring to Vimeo.")
    if row["zoom_cloud_deleted_at"]:
        raise ValidationError("This Zoom recording has already been deleted.")
    await ensure_safe_to_delete(db, dict(row))
    return {"recording_id": recording_id, "ready": True}
