"""Zoom host-pool, embedded joining, attendance, and Vimeo recording processing."""
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import tempfile
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode

import httpx
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ForbiddenError, NotFoundError, ValidationError

ZOOM_API = "https://api.zoom.us/v2"
ZOOM_AUTHORIZE = "https://zoom.us/oauth/authorize"
ZOOM_TOKEN = "https://zoom.us/oauth/token"
logger = logging.getLogger(__name__)


def _fernet() -> Fernet:
    try:
        return Fernet(settings.ZOOM_TOKEN_ENCRYPTION_KEY.encode())
    except (ValueError, TypeError) as exc:
        raise ValidationError("ZOOM_TOKEN_ENCRYPTION_KEY is not configured correctly") from exc


def _encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def _decrypt(value: str) -> str:
    return _fernet().decrypt(value.encode()).decode()


def _configured() -> bool:
    return bool(settings.ZOOM_CLIENT_ID and settings.ZOOM_CLIENT_SECRET and settings.ZOOM_TOKEN_ENCRYPTION_KEY)


def _zak_token_url(host: dict) -> str:
    zoom_user_id = quote(str(host.get("zoom_user_id") or "me"), safe="")
    return f"{ZOOM_API}/users/{zoom_user_id}/token"


def _zoom_error(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    code = payload.get("code")
    message = str(payload.get("message") or "").strip()
    detail = ": ".join(part for part in (f"code {code}" if code is not None else "", message) if part)
    return detail or f"HTTP {response.status_code}"


def _recording_download_token(payload: dict, oauth_token: str) -> str:
    """Prefer Zoom's short-lived recording webhook token for webhook download URLs."""
    return str(payload.get("download_token") or oauth_token)


def _recording_api_ref(meeting_uuid: str) -> str:
    """Zoom requires meeting UUIDs in recording paths to be URL encoded twice."""
    return quote(quote(str(meeting_uuid), safe=""), safe="")


_RECORDING_TYPE_PRIORITY = {
    "shared_screen_with_speaker_view(CC)": 0,
    "shared_screen_with_speaker_view": 1,
    "shared_screen_with_gallery_view": 2,
    "shared_screen": 3,
    "active_speaker": 4,
    "gallery_view": 5,
    "host_video": 6,
}


def _recording_type_priority(recording_type: str | None) -> int:
    return _RECORDING_TYPE_PRIORITY.get(str(recording_type or ""), 99)


def _preferred_recording_files(files: list[dict]) -> list[dict]:
    """Return one useful Zoom video layout, retaining split parts of that layout."""
    mp4_files = [
        item for item in files
        if str(item.get("file_type") or item.get("file_extension") or "").upper() == "MP4"
        and item.get("download_url")
        and str(item.get("status") or "completed").lower() == "completed"
        and int(item.get("file_size") or 1) > 0
    ]
    if not mp4_files:
        return []
    selected_type = min(
        {str(item.get("recording_type") or "") for item in mp4_files},
        key=_recording_type_priority,
    )
    return sorted(
        (item for item in mp4_files if str(item.get("recording_type") or "") == selected_type),
        key=lambda item: (str(item.get("recording_start") or ""), str(item.get("id") or "")),
    )


async def get_settings(db: AsyncSession) -> dict:
    row = (await db.execute(text("SELECT * FROM lms_zoom_settings WHERE settings_id=1"))).mappings().first()
    hosts = (await db.execute(text("""
        SELECT connection_id,email,zoom_user_id,capacity,enabled,connected_at,updated_at
        FROM lms_zoom_host_connections ORDER BY connection_id
    """))).mappings().all()
    return {
        "enabled": bool(row and row["enabled"]), "active_provider": row["active_provider"] if row else "google",
        "attendance_sync_enabled": bool(row is None or row["attendance_sync_enabled"]),
        "attendance_threshold_percentage": row["attendance_threshold_percentage"] if row else 50,
        "automatic_recording": bool(row is None or row["automatic_recording"]),
        "oauth_configured": _configured(),
        "sdk_configured": bool(settings.ZOOM_MEETING_SDK_KEY and settings.ZOOM_MEETING_SDK_SECRET),
        "webhook_configured": bool(settings.ZOOM_WEBHOOK_SECRET), "oauth_redirect_uri": settings.ZOOM_REDIRECT_URI,
        "hosts": [dict(item) for item in hosts],
    }


async def update_settings(db: AsyncSession, payload: dict, user_id: int) -> dict:
    enabled = bool(payload.get("enabled", False))
    if enabled and not _configured():
        raise ValidationError("Add the Zoom OAuth and token-encryption credentials to the API environment first")
    threshold = max(1, min(100, int(payload.get("attendance_threshold_percentage", 50))))
    provider = payload.get("active_provider", "zoom" if enabled else "google")
    if provider not in {"google", "zoom"}:
        raise ValidationError("Invalid active meeting provider")
    await db.execute(text("""
      INSERT INTO lms_zoom_settings(settings_id,enabled,active_provider,attendance_sync_enabled,
        attendance_threshold_percentage,automatic_recording,updated_by,updated_at)
      VALUES(1,:enabled,:provider,:attendance,:threshold,:recording,:user_id,now())
      ON CONFLICT(settings_id) DO UPDATE SET enabled=EXCLUDED.enabled,active_provider=EXCLUDED.active_provider,
        attendance_sync_enabled=EXCLUDED.attendance_sync_enabled,
        attendance_threshold_percentage=EXCLUDED.attendance_threshold_percentage,
        automatic_recording=EXCLUDED.automatic_recording,updated_by=EXCLUDED.updated_by,updated_at=now()
    """), {"enabled": enabled, "provider": provider, "attendance": bool(payload.get("attendance_sync_enabled", True)),
             "threshold": threshold, "recording": bool(payload.get("automatic_recording", True)), "user_id": user_id})
    await db.commit()
    return await get_settings(db)


async def begin_connection(db: AsyncSession, user_id: int) -> str:
    if not _configured():
        raise ValidationError("Zoom OAuth credentials are not configured")
    raw = secrets.token_urlsafe(48)
    await db.execute(text("DELETE FROM lms_zoom_oauth_states WHERE requested_by=:user OR expires_at<now()"), {"user": user_id})
    await db.execute(text("""
      INSERT INTO lms_zoom_oauth_states(state_hash,requested_by,expires_at)
      VALUES(:hash,:user,:expires)
    """), {"hash": hashlib.sha256(raw.encode()).hexdigest(), "user": user_id,
             "expires": datetime.now(timezone.utc) + timedelta(minutes=settings.ZOOM_OAUTH_STATE_EXPIRE_MINUTES)})
    await db.commit()
    return f"{ZOOM_AUTHORIZE}?{urlencode({'response_type':'code','client_id':settings.ZOOM_CLIENT_ID,'redirect_uri':settings.ZOOM_REDIRECT_URI,'state':raw})}"


async def complete_connection(db: AsyncSession, code: str, state: str) -> str:
    state_hash = hashlib.sha256(state.encode()).hexdigest()
    valid = (await db.execute(text("""
      DELETE FROM lms_zoom_oauth_states WHERE state_hash=:hash AND expires_at>now()
      RETURNING requested_by
    """), {"hash": state_hash})).scalar_one_or_none()
    if valid is None:
        raise ValidationError("The Zoom connection link is invalid or expired")
    auth = (settings.ZOOM_CLIENT_ID, settings.ZOOM_CLIENT_SECRET)
    async with httpx.AsyncClient(timeout=25) as client:
        token_response = await client.post(ZOOM_TOKEN, auth=auth, data={"grant_type":"authorization_code", "code":code,
            "redirect_uri":settings.ZOOM_REDIRECT_URI})
        if token_response.is_error:
            # While the Zoom app is unpublished only users inside the developer's
            # own Zoom account may authorise it, which is the usual cause here.
            raise ValidationError(
                "Zoom could not complete the account connection. Until the Zoom app is "
                "published, the host must be a user in the same Zoom account as the app, "
                "so add the new licence as a user in your Zoom organisation first."
            )
        tokens = token_response.json()
        profile_response = await client.get(f"{ZOOM_API}/users/me", headers={"Authorization":f"Bearer {tokens['access_token']}"})
        if profile_response.is_error:
            raise ValidationError("The connected Zoom host could not be verified")
        profile = profile_response.json()
    refresh = tokens.get("refresh_token")
    if not refresh:
        raise ValidationError("Zoom did not return a refresh token")
    await db.execute(text("""
      INSERT INTO lms_zoom_host_connections(zoom_user_id,zoom_account_id,email,encrypted_access_token,
        encrypted_refresh_token,token_expires_at,connected_by,updated_at)
      VALUES(:zoom_user,:account,:email,:access,:refresh,:expires,:user,now())
      ON CONFLICT(zoom_user_id) DO UPDATE SET email=EXCLUDED.email,zoom_account_id=EXCLUDED.zoom_account_id,
        encrypted_access_token=EXCLUDED.encrypted_access_token,encrypted_refresh_token=EXCLUDED.encrypted_refresh_token,
        token_expires_at=EXCLUDED.token_expires_at,enabled=TRUE,connected_by=EXCLUDED.connected_by,updated_at=now()
    """), {"zoom_user": str(profile["id"]), "account": str(profile.get("account_id") or ""),
             "email": profile["email"].lower(), "access": _encrypt(tokens["access_token"]), "refresh": _encrypt(refresh),
             "expires": datetime.now(timezone.utc)+timedelta(seconds=int(tokens.get("expires_in",3600))), "user": valid})
    await db.commit()
    return profile["email"]


async def update_host(db: AsyncSession, connection_id: int, capacity: int, enabled: bool) -> dict:
    if capacity not in {1, 2}:
        raise ValidationError("Zoom host capacity must be one or two")
    row = (await db.execute(text("""
      UPDATE lms_zoom_host_connections SET capacity=:capacity,enabled=:enabled,updated_at=now()
      WHERE connection_id=:id RETURNING connection_id,email,zoom_user_id,capacity,enabled,connected_at,updated_at
    """), {"capacity": capacity, "enabled": enabled, "id": connection_id})).mappings().first()
    if row is None:
        raise NotFoundError("Zoom host account not found")
    await db.commit(); return dict(row)


async def remove_host(db: AsyncSession, connection_id: int) -> None:
    used = await db.scalar(text("SELECT 1 FROM lms_online_meetings WHERE zoom_host_connection_id=:id AND status='scheduled' LIMIT 1"), {"id":connection_id})
    if used:
        raise ValidationError("Disable or reassign this host's scheduled meetings before removing it")
    await db.execute(text("DELETE FROM lms_zoom_host_connections WHERE connection_id=:id"), {"id":connection_id})
    await db.commit()


async def _access_token(db: AsyncSession, host: dict) -> str:
    if host["token_expires_at"] > datetime.now(timezone.utc)+timedelta(minutes=2):
        return _decrypt(host["encrypted_access_token"])
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(ZOOM_TOKEN, auth=(settings.ZOOM_CLIENT_ID,settings.ZOOM_CLIENT_SECRET),
            data={"grant_type":"refresh_token","refresh_token":_decrypt(host["encrypted_refresh_token"])})
    if response.is_error:
        await db.execute(text("UPDATE lms_zoom_host_connections SET enabled=FALSE WHERE connection_id=:id"), {"id":host["connection_id"]}); await db.commit()
        raise ValidationError(f"Reconnect Zoom host {host['email']}")
    tokens=response.json(); refresh=tokens.get("refresh_token") or _decrypt(host["encrypted_refresh_token"])
    await db.execute(text("""UPDATE lms_zoom_host_connections SET encrypted_access_token=:access,
      encrypted_refresh_token=:refresh,token_expires_at=:expires,updated_at=now() WHERE connection_id=:id"""),
      {"access":_encrypt(tokens["access_token"]),"refresh":_encrypt(refresh),"expires":datetime.now(timezone.utc)+timedelta(seconds=int(tokens.get("expires_in",3600))),"id":host["connection_id"]})
    await db.commit(); return tokens["access_token"]


async def window_availability(db: AsyncSession, start: datetime, end: datetime, exclude_meeting: int|None=None) -> dict:
    """Concurrent live-class capacity for a time window.

    The simultaneous limit is the sum of the capacity of every enabled host
    account, so connecting another Zoom account raises it with no code change.
    A host already at its own capacity cannot absorb more, so availability is
    aggregated per host rather than compared against the pool total.
    """
    hosts=(await db.execute(text("""
      SELECT h.connection_id, h.email, h.capacity,
        (SELECT count(*) FROM lms_online_meetings m WHERE m.zoom_host_connection_id=h.connection_id
          AND m.provider='zoom' AND m.status='scheduled' AND m.start_time<:end_time AND m.end_time>:start_time
          AND (CAST(:exclude_id AS BIGINT) IS NULL OR m.meeting_id<>CAST(:exclude_id AS BIGINT))) AS concurrent
      FROM lms_zoom_host_connections h WHERE h.enabled=TRUE ORDER BY h.connection_id
    """),{"start_time":start,"end_time":end,"exclude_id":exclude_meeting})).mappings().all()
    clashes=(await db.execute(text("""
      SELECT m.meeting_id,m.title,m.start_time,m.end_time,lc.code AS course_code,c.code AS class_code,c.name AS class_name,u.full_name AS lecturer_name
      FROM lms_online_meetings m JOIN lms_classes c ON c.class_id=m.class_id
      JOIN lms_courses lc ON lc.course_id=c.course_id
      LEFT JOIN users u ON u.user_id=m.lecturer_user_id
      WHERE m.provider='zoom' AND m.status='scheduled' AND m.start_time<:end_time AND m.end_time>:start_time
        AND (CAST(:exclude_id AS BIGINT) IS NULL OR m.meeting_id<>CAST(:exclude_id AS BIGINT))
      ORDER BY m.start_time
    """),{"start_time":start,"end_time":end,"exclude_id":exclude_meeting})).mappings().all()
    total=sum(host["capacity"] for host in hosts)
    available=sum(max(0,host["capacity"]-host["concurrent"]) for host in hosts)
    return {
        "host_count":len(hosts),
        "total_capacity":total,
        "used":min(len(clashes),total),
        "available":available,
        "overlapping":[{
            "meeting_id":row["meeting_id"],"title":row["title"],
            "start_time":row["start_time"],"end_time":row["end_time"],
            "course_code":row["course_code"],"class_code":row["class_code"],"class_name":row["class_name"],
            "lecturer_name":row["lecturer_name"],
        } for row in clashes],
    }


async def live_status(db: AsyncSession) -> dict:
    """Classes running right now, plus the pool's simultaneous limit."""
    now=datetime.now(timezone.utc)
    rows=(await db.execute(text("""
      SELECT m.meeting_id,m.title,m.start_time,m.end_time,lc.code AS course_code,c.code AS class_code,c.name AS class_name,u.full_name AS lecturer_name
      FROM lms_online_meetings m JOIN lms_classes c ON c.class_id=m.class_id
      JOIN lms_courses lc ON lc.course_id=c.course_id
      LEFT JOIN users u ON u.user_id=m.lecturer_user_id
      WHERE m.provider='zoom' AND m.status='scheduled' AND m.start_time<=:now AND m.end_time>=:now
      ORDER BY m.start_time
    """),{"now":now})).mappings().all()
    capacity=(await db.execute(text("""
      SELECT count(*) AS hosts, COALESCE(sum(capacity),0) AS total
      FROM lms_zoom_host_connections WHERE enabled=TRUE
    """))).mappings().first()
    return {
        "live_count":len(rows),
        "host_count":capacity["hosts"],
        "total_capacity":int(capacity["total"]),
        "live":[dict(row) for row in rows],
    }


async def _claim_host(db: AsyncSession, lecturer_id: int, start: datetime, end: datetime, exclude_meeting: int|None=None) -> dict:
    rows=(await db.execute(text("""
      SELECT h.*, CASE WHEN EXISTS(SELECT 1 FROM lms_online_meetings prior WHERE prior.zoom_host_connection_id=h.connection_id
        AND prior.lecturer_user_id=:lecturer) THEN 0 ELSE 1 END AS preference,
        (SELECT count(*) FROM lms_online_meetings m WHERE m.zoom_host_connection_id=h.connection_id
          AND m.provider='zoom' AND m.status='scheduled' AND m.start_time<:end_time AND m.end_time>:start_time
          AND (CAST(:exclude_id AS BIGINT) IS NULL OR m.meeting_id<>CAST(:exclude_id AS BIGINT))) AS concurrent
      FROM lms_zoom_host_connections h WHERE h.enabled=TRUE ORDER BY preference,h.connection_id FOR UPDATE OF h
    """), {"lecturer":lecturer_id,"start_time":start,"end_time":end,"exclude_id":exclude_meeting})).mappings().all()
    host=next((dict(row) for row in rows if row["concurrent"] < row["capacity"]),None)
    if host is None:
        if not rows:
            raise ValidationError("No Zoom host account is connected. Connect a host in LMS Settings before scheduling a live class.")
        limit=sum(row["capacity"] for row in rows)
        raise ValidationError(
            f"This time clashes with other live classes. All {limit} simultaneous "
            f"class slots across {len(rows)} connected Zoom host account(s) are already booked. "
            "Choose another time or connect another Zoom account to raise the limit."
        )
    return host


async def create_zoom_meeting(db: AsyncSession, payload, lecturer_id: int, class_, course) -> dict:
    zoom_settings=(await db.execute(text("SELECT * FROM lms_zoom_settings WHERE settings_id=1"))).mappings().first()
    if not zoom_settings or not zoom_settings["enabled"]: raise ValidationError("Zoom integration is not enabled")
    host=await _claim_host(db,lecturer_id,payload.start_time,payload.end_time); token=await _access_token(db,host)
    duration=max(1,round((payload.end_time-payload.start_time).total_seconds()/60))
    options=payload.options
    passcode=options.passcode or secrets.token_hex(4)
    # The integration setting is the default; the schedule form may override it.
    recording=options.auto_recording or ("cloud" if zoom_settings["automatic_recording"] else "none")
    body={"topic":payload.title.strip(),"type":2,"start_time":payload.start_time.isoformat(),"duration":duration,"password":passcode,
      "timezone":payload.timezone or class_.timezone,"agenda":(payload.description or "").strip(),
      # No registration: students are already authenticated LMS users and join
      # through the embedded Meeting SDK, so Zoom must not demand a registrant.
      "settings":{"approval_type":2,"waiting_room":options.waiting_room,
        "join_before_host":options.join_before_host,"jbh_time":0,
        "mute_upon_entry":options.mute_upon_entry,"host_video":options.host_video,
        "participant_video":options.participant_video,"audio":"both",
        "meeting_authentication":False,"auto_recording":recording}}
    async with httpx.AsyncClient(timeout=30) as client:
        response=await client.post(f"{ZOOM_API}/users/me/meetings",headers={"Authorization":f"Bearer {token}"},json=body)
    if response.is_error: raise ValidationError("Zoom could not schedule the meeting. Check the host licence and app scopes.")
    remote=response.json()
    return {"provider":"zoom","join_uri":remote.get("join_url", ""),"provider_meeting_id":str(remote["id"]),
      "provider_meeting_uuid":remote.get("uuid"),"zoom_host_connection_id":host["connection_id"],
      "zoom_passcode_encrypted":_encrypt(passcode),
      "processing_status":"waiting","calendar_sync_status":"disabled","calendar_sync_error":None,
      "google_space_name":None,"google_meeting_uri":None,"google_meeting_code":None,
      "google_calendar_event_id":None,"google_calendar_event_uri":None,"students_notified":False}


async def update_zoom_meeting(db: AsyncSession, meeting, payload) -> dict:
    """Update a remote meeting while retaining its allocated pooled host."""
    host=(await db.execute(text("SELECT * FROM lms_zoom_host_connections WHERE connection_id=:id AND enabled=TRUE"),{"id":meeting.zoom_host_connection_id})).mappings().first()
    if not host:
        raise ValidationError("The allocated Zoom host is disconnected. Reconnect it before editing this meeting.")
    conflicts=await db.scalar(text("""SELECT count(*) FROM lms_online_meetings
      WHERE zoom_host_connection_id=:host AND provider='zoom' AND status='scheduled'
      AND meeting_id<>:meeting AND start_time<:end_time AND end_time>:start_time"""),
      {"host":host["connection_id"],"meeting":meeting.meeting_id,"start_time":payload.start_time,"end_time":payload.end_time})
    if conflicts >= host["capacity"]:
        # The remote meeting belongs to this host account, so it cannot be moved
        # to a different host in the pool the way a new booking can.
        raise ValidationError(
            f"The Zoom account hosting this class ({host['email']}) already runs "
            f"{host['capacity']} class(es) at the new time. Choose another time, or cancel "
            "this class and schedule it again to have it allocated to a free host."
        )
    token=await _access_token(db,dict(host))
    options=payload.options
    tz = payload.timezone or meeting.timezone or "UTC"
    # Zoom's PATCH /meetings/{id} expects start_time in the meeting's own timezone
    # as a naive local datetime (no UTC offset).  Python's isoformat() emits "+00:00"
    # for aware datetimes which Zoom rejects with a 400 error.  Convert to the
    # target timezone first, then strip the offset so Zoom reads it relative to
    # the timezone field we send alongside it.
    try:
        import zoneinfo
        local_start = payload.start_time.astimezone(zoneinfo.ZoneInfo(tz))
    except Exception:
        local_start = payload.start_time
    start_str = local_start.strftime("%Y-%m-%dT%H:%M:%S")
    body={"topic":payload.title.strip(),"start_time":start_str,
      "duration":max(1,round((payload.end_time-payload.start_time).total_seconds()/60)),
      "timezone":tz,"agenda":(payload.description or "").strip(),
      "settings":{"waiting_room":options.waiting_room,"join_before_host":options.join_before_host,
        "mute_upon_entry":options.mute_upon_entry,"host_video":options.host_video,
        "participant_video":options.participant_video}}
    async with httpx.AsyncClient(timeout=25) as client:
        response=await client.patch(f"{ZOOM_API}/meetings/{meeting.provider_meeting_id}",headers={"Authorization":f"Bearer {token}"},json=body)
    if response.is_error:
        detail = _zoom_error(response)
        logger.error("Zoom PATCH meeting %s failed: %s — body: %s", meeting.provider_meeting_id, detail, response.text[:500])
        raise ValidationError(f"Zoom could not update this meeting ({detail}). Check the host connection and try again.")
    return {"title":payload.title.strip(),"description":(payload.description or "").strip() or None,
      "start_time":payload.start_time,"end_time":payload.end_time,
      "timezone":tz,"processing_error":None}


async def cancel_zoom_meeting(db: AsyncSession, meeting) -> None:
    host=(await db.execute(text("SELECT * FROM lms_zoom_host_connections WHERE connection_id=:id"),{"id":meeting.zoom_host_connection_id})).mappings().first()
    if not host:
        raise ValidationError("The allocated Zoom host connection no longer exists")
    token=await _access_token(db,dict(host))
    async with httpx.AsyncClient(timeout=25) as client:
        response=await client.delete(f"{ZOOM_API}/meetings/{meeting.provider_meeting_id}",headers={"Authorization":f"Bearer {token}"})
    if response.is_error and response.status_code != 404:
        raise ValidationError("Zoom could not cancel this meeting. Try again.")


def _sdk_signature(meeting_number: str, role: int) -> str:
    if not settings.ZOOM_MEETING_SDK_KEY or not settings.ZOOM_MEETING_SDK_SECRET: raise ValidationError("Zoom Meeting SDK credentials are not configured")
    now=int(datetime.now(timezone.utc).timestamp())-30; exp=now+7200
    header={"alg":"HS256","typ":"JWT"}; payload={"sdkKey":settings.ZOOM_MEETING_SDK_KEY,"mn":meeting_number,"role":role,"iat":now,"exp":exp,"appKey":settings.ZOOM_MEETING_SDK_KEY,"tokenExp":exp}
    enc=lambda value: base64.urlsafe_b64encode(json.dumps(value,separators=(',',':')).encode()).decode().rstrip('=')
    unsigned=f"{enc(header)}.{enc(payload)}"; sig=base64.urlsafe_b64encode(hmac.new(settings.ZOOM_MEETING_SDK_SECRET.encode(),unsigned.encode(),hashlib.sha256).digest()).decode().rstrip('=')
    return f"{unsigned}.{sig}"


async def _lecturer_can_host(db: AsyncSession, meeting_id: int, primary_class_id: int,
                             _meeting_lecturer_id: int, user_id: int) -> bool:
    return bool(await db.scalar(text("""SELECT 1
          FROM (
            SELECT class_id FROM lms_meeting_audience_classes WHERE meeting_id=:meeting_id
            UNION SELECT :primary_class_id
          ) audience
          JOIN lms_classes class_ ON class_.class_id=audience.class_id
          JOIN lms_class_lecturers class_lecturer
            ON class_lecturer.class_id=audience.class_id AND class_lecturer.lecturer_user_id=:user_id
          WHERE class_.status<>'cancelled'
          LIMIT 1"""),{
            "meeting_id":meeting_id,"primary_class_id":primary_class_id,"user_id":user_id,
        }))


async def join_config(db: AsyncSession, meeting_id: int, user_id: int, role: str) -> dict:
    meeting=(await db.execute(text("SELECT * FROM lms_online_meetings WHERE meeting_id=:id AND provider='zoom' AND status<>'cancelled'"),{"id":meeting_id})).mappings().first()
    if not meeting: raise NotFoundError("Zoom meeting not found")
    lecturer_host=role=="LECTURER" and await _lecturer_can_host(
        db,meeting_id,meeting["class_id"],meeting["lecturer_user_id"],user_id
    )
    is_host=role in {"SUPER_ADMIN","ADMIN"} or lecturer_host
    if not is_host:
        allowed=await db.scalar(text("""SELECT 1
          FROM (
            SELECT class_id FROM lms_meeting_audience_classes WHERE meeting_id=:m
            UNION SELECT :primary_class_id
          ) audience
          JOIN lms_classes class_ ON class_.class_id=audience.class_id
          JOIN lms_courses course ON course.course_id=class_.course_id
          JOIN lms_class_students cs
            ON cs.class_id=audience.class_id AND cs.student_user_id=:u
          JOIN lms_course_enrollments enrollment
            ON enrollment.course_id=class_.course_id
           AND enrollment.student_user_id=:u
           AND enrollment.status='enrolled'
          WHERE class_.status<>'cancelled' AND course.status<>'archived'
          LIMIT 1"""),{"m":meeting_id,"primary_class_id":meeting["class_id"],"u":user_id})
        if not allowed: raise ForbiddenError("You are not enrolled in this meeting's audience")
    user=(await db.execute(text("SELECT full_name,email FROM users WHERE user_id=:id AND is_active"),{"id":user_id})).mappings().first()
    result={"meeting_id":meeting_id,"meeting_number":meeting["provider_meeting_id"],"sdk_key":settings.ZOOM_MEETING_SDK_KEY,
      "signature":_sdk_signature(meeting["provider_meeting_id"],1 if is_host else 0),"role":1 if is_host else 0,
      "user_name":user["full_name"] or user["email"],"user_email":user["email"],
      "password":_decrypt(meeting["zoom_passcode_encrypted"]) if meeting["zoom_passcode_encrypted"] else ""}
    if is_host:
        host=(await db.execute(text("SELECT * FROM lms_zoom_host_connections WHERE connection_id=:id"),{"id":meeting["zoom_host_connection_id"]})).mappings().first(); token=await _access_token(db,dict(host))
        async with httpx.AsyncClient(timeout=20) as client: response=await client.get(_zak_token_url(dict(host)),params={"type":"zak"},headers={"Authorization":f"Bearer {token}"})
        if response.is_error: raise ValidationError(f"Zoom could not issue the lecturer host token ({_zoom_error(response)}). Reconnect this host after granting user:read:zak.")
        result["zak"]=response.json().get("token")
    # Students join through the embedded SDK using the signature alone. Meetings
    # are created without Zoom registration, so no registrant token is involved.
    return result


async def mark_end_requested(db: AsyncSession, meeting_id: int, user_id: int, role: str) -> dict:
    meeting=(await db.execute(text("SELECT * FROM lms_online_meetings WHERE meeting_id=:id AND provider='zoom' AND status='scheduled'"),{"id":meeting_id})).mappings().first()
    if not meeting: raise NotFoundError("Scheduled Zoom meeting not found")
    lecturer_host=role=="LECTURER" and await _lecturer_can_host(
        db,meeting_id,meeting["class_id"],meeting["lecturer_user_id"],user_id
    )
    if role not in {"SUPER_ADMIN","ADMIN"} and not lecturer_host:
        raise ForbiddenError("Only this meeting's host can end it for everyone")
    await db.execute(text("""UPDATE lms_online_meetings
      SET processing_status='end_requested',processing_error=NULL,updated_at=now()
      WHERE meeting_id=:id AND status='scheduled'"""),{"id":meeting_id})
    await db.commit()
    return {"meeting_id":meeting_id,"end_requested":True}


async def confirm_end_requested(db: AsyncSession, meeting_id: int, user_id: int, role: str) -> dict:
    meeting=(await db.execute(text("""SELECT * FROM lms_online_meetings
      WHERE meeting_id=:id AND provider='zoom' AND status IN ('scheduled','completed')"""),{"id":meeting_id})).mappings().first()
    if not meeting: raise NotFoundError("Zoom meeting not found")
    lecturer_host=role=="LECTURER" and await _lecturer_can_host(
        db,meeting_id,meeting["class_id"],meeting["lecturer_user_id"],user_id
    )
    if role not in {"SUPER_ADMIN","ADMIN"} and not lecturer_host:
        raise ForbiddenError("Only this meeting's host can end it for everyone")
    if meeting["status"] == "completed":
        return {"meeting_id":meeting_id,"status":"completed"}
    confirmed=await db.scalar(text("""SELECT 1 FROM lms_online_meetings
      WHERE meeting_id=:id AND status='scheduled'
        AND processing_status='end_requested'
        AND updated_at >= now()-interval '2 minutes'"""),{"id":meeting_id})
    if not confirmed:
        raise ValidationError("End Meeting for All was not confirmed")
    await db.execute(text("""UPDATE lms_online_meetings
      SET status='completed',processing_status='attendance_pending',processing_error=NULL,updated_at=now()
      WHERE meeting_id=:id AND status='scheduled'"""),{"id":meeting_id})
    await db.execute(text("""INSERT INTO lms_zoom_jobs(event_key,meeting_id,job_type,payload,available_at)
      VALUES(:key,:meeting,'attendance',CAST(:payload AS jsonb),now()+interval '2 minutes')
      ON CONFLICT(event_key) DO NOTHING"""),{
        "key":f"lms:end-confirmed:{meeting_id}","meeting":meeting_id,
        "payload":json.dumps({"event":"lms.end_confirmed","meeting_id":meeting_id}),
    })
    await db.commit()
    return {"meeting_id":meeting_id,"status":"completed"}


def verify_webhook(timestamp: str, body: bytes, signature: str) -> bool:
    message=f"v0:{timestamp}:{body.decode()}"; expected="v0="+hmac.new(settings.ZOOM_WEBHOOK_SECRET.encode(),message.encode(),hashlib.sha256).hexdigest()
    return bool(settings.ZOOM_WEBHOOK_SECRET and hmac.compare_digest(expected,signature))


def webhook_validation_token(plain_token: str) -> str:
    if not settings.ZOOM_WEBHOOK_SECRET:
        raise ValidationError("Zoom webhook secret is not configured")
    return hmac.new(
        settings.ZOOM_WEBHOOK_SECRET.encode(), plain_token.encode(), hashlib.sha256
    ).hexdigest()


async def receive_webhook(db: AsyncSession, event: dict) -> None:
    name=event.get("event",""); obj=event.get("payload",{}).get("object",{}); meeting_id=str(obj.get("id") or "")
    local=await db.scalar(text("SELECT meeting_id FROM lms_online_meetings WHERE provider='zoom' AND provider_meeting_id=:id ORDER BY meeting_id DESC LIMIT 1"),{"id":meeting_id})
    if not local: return
    kinds=[]
    if name=="meeting.ended":
        # Zoom also emits meeting.ended when a lone host chooses Leave Meeting.
        # The webhook has no field that distinguishes that from End for All, so
        # accept it only after the LMS host explicitly signalled that action.
        confirmed_end=await db.scalar(text("""SELECT 1 FROM lms_online_meetings
          WHERE meeting_id=:id AND status='scheduled'
            AND processing_status='end_requested'
            AND updated_at >= now()-interval '2 minutes'"""),{"id":local})
        if confirmed_end:
            # The meeting lifecycle must not wait for Zoom's participant report.
            # That report can take several minutes and its retry job is independent.
            await db.execute(text("""UPDATE lms_online_meetings
              SET status='completed',processing_status='attendance_pending',processing_error=NULL,updated_at=now()
              WHERE meeting_id=:id AND status='scheduled'"""),{"id":local})
            kinds.append("attendance")
    if name=="recording.completed":
        # A host can leave while participants continue the meeting. Zoom may
        # finish a recording segment at that point, so recording completion is
        # never evidence that the meeting ended for everyone. Only the
        # meeting.ended webhook owns the meeting lifecycle status.
        kinds.append("recording")
    for kind in kinds:
        key=f"{name}:{obj.get('uuid') or meeting_id}:{kind}"
        await db.execute(text("""INSERT INTO lms_zoom_jobs(event_key,meeting_id,job_type,payload,available_at)
          VALUES(:key,:meeting,:kind,CAST(:payload AS jsonb),now()+interval '2 minutes') ON CONFLICT(event_key) DO NOTHING"""),
          {"key":key,"meeting":local,"kind":kind,"payload":json.dumps(event)})
    await db.commit()


def _past_meeting_refs(context) -> list[str]:
    """Candidate Zoom identifiers for the finished instance, best first.

    A UUID that starts with a slash or contains a double slash has to be encoded
    twice, otherwise Zoom reads the path as extra segments and rejects the call.
    """
    refs: list[str] = []
    uuid = str(context["provider_meeting_uuid"] or "")
    if uuid:
        once = quote(uuid, safe="")
        refs.append(quote(once, safe="") if uuid.startswith("/") or "//" in uuid else once)
    if context["provider_meeting_id"]:
        refs.append(quote(str(context["provider_meeting_id"]), safe=""))
    return refs


async def _fetch_participant_page(client: httpx.AsyncClient, path: str, token: str, page: str | None):
    params = {"page_size": 300}
    if page:
        params["next_page_token"] = page
    return await client.get(f"{ZOOM_API}/{path}", params=params, headers={"Authorization": f"Bearer {token}"})


async def _past_participants(context, token: str) -> list[dict]:
    """Every participant row for a finished meeting, across all report pages.

    The participant report is written after the meeting is torn down, so a call
    made too early legitimately returns nothing yet. Report and past-meeting
    endpoints are both tried because which one a Zoom plan exposes differs.
    """
    failures: list[str] = []
    async with httpx.AsyncClient(timeout=30) as client:
        for ref in _past_meeting_refs(context):
            for path in (f"report/meetings/{ref}/participants", f"past_meetings/{ref}/participants"):
                participants: list[dict] = []
                page: str | None = None
                while True:
                    response = await _fetch_participant_page(client, path, token, page)
                    if response.is_error:
                        failures.append(f"{path.split('/')[0]}: {_zoom_error(response)}")
                        break
                    body = response.json()
                    participants.extend(body.get("participants", []))
                    page = body.get("next_page_token") or None
                    if not page:
                        return participants
    message = (
        "Zoom has not published the participant report for this class yet. Retry in a "
        "few minutes. If it keeps failing, the connected Zoom account needs a paid plan "
        "and the report:read:admin scope."
    )
    detail = "; ".join(dict.fromkeys(failures))
    raise ValidationError(f"{message} Zoom said — {detail}" if detail else message)


async def sync_attendance(db: AsyncSession, meeting_id: int, synced_by: int) -> None:
    context=(await db.execute(text("""SELECT m.*,s.attendance_threshold_percentage FROM lms_online_meetings m
      JOIN lms_zoom_settings s ON s.settings_id=1 WHERE m.meeting_id=:id AND m.provider='zoom'"""),{"id":meeting_id})).mappings().first()
    if not context: raise NotFoundError("Zoom meeting not found")
    host=(await db.execute(text("SELECT * FROM lms_zoom_host_connections WHERE connection_id=:id"),{"id":context["zoom_host_connection_id"]})).mappings().first()
    if not host:
        raise ValidationError("The Zoom host account for this class is no longer connected, so attendance cannot be imported. Reconnect it in LMS Settings.")
    token=await _access_token(db,dict(host))
    participants=await _past_participants(context,token); actual_start=min((_dt(p.get("join_time")) for p in participants if p.get("join_time")),default=context["start_time"]); actual_end=max((_dt(p.get("leave_time")) for p in participants if p.get("leave_time")),default=context["end_time"]); window=max(1,int((actual_end-actual_start).total_seconds()))
    roster=(await db.execute(text("""SELECT u.user_id,lower(u.email) email,u.full_name FROM lms_class_students cs JOIN users u ON u.user_id=cs.student_user_id
      WHERE cs.class_id=:class_id AND cs.assigned_at<=:ended AND u.is_active"""),{"class_id":context["class_id"],"ended":actual_end})).mappings().all()
    buckets={row["email"]:[] for row in roster}; unmatched=[]
    for p in participants:
        email=str(p.get("user_email") or "").lower(); duration=int(p.get("duration") or 0)
        if email in buckets: buckets[email].append(p)
        else: unmatched.append({"display_name":p.get("name") or "Unknown participant","participant_type":"zoom","attended_seconds":duration})
    session=await db.scalar(text("""INSERT INTO lms_attendance_sessions(meeting_id,class_id,provider_reference,actual_start_time,actual_end_time,threshold_percentage,sync_status,unmatched_participants,synced_by,synced_at)
      VALUES(:m,:c,:ref,:start,:end,:threshold,'synced',CAST(:unmatched AS jsonb),:by,now()) ON CONFLICT(meeting_id) DO UPDATE SET provider_reference=EXCLUDED.provider_reference,
      actual_start_time=EXCLUDED.actual_start_time,actual_end_time=EXCLUDED.actual_end_time,threshold_percentage=EXCLUDED.threshold_percentage,sync_status='synced',sync_error=NULL,unmatched_participants=EXCLUDED.unmatched_participants,synced_by=EXCLUDED.synced_by,synced_at=now() RETURNING attendance_session_id"""),
      {"m":meeting_id,"c":context["class_id"],"ref":context["provider_meeting_uuid"],"start":actual_start,"end":actual_end,"threshold":context["attendance_threshold_percentage"],"unmatched":json.dumps(unmatched),"by":synced_by})
    from app.modules.lms.attendance_service import _attendance_status, _merge_duration
    for student in roster:
        entries=buckets[student["email"]]
        # Zoom reports one row per join, so a student who reconnects or is signed
        # in on two devices appears several times. Merge the intervals instead of
        # summing durations so overlapping sessions are not counted twice.
        intervals=[(_dt(p.get("join_time")),_dt(p.get("leave_time")) or actual_end) for p in entries if p.get("join_time")]
        seconds,first_join,last_leave=_merge_duration(intervals,actual_start,actual_end)
        percentage=min(100,round(seconds*100/window,2))
        status=_attendance_status(seconds,window,context["attendance_threshold_percentage"])
        await db.execute(text("""INSERT INTO lms_attendance_records(attendance_session_id,student_user_id,status,attended_seconds,attendance_percentage,first_join_time,last_leave_time,google_participant_name,source)
          VALUES(:session,:student,:status,:seconds,:percentage,:first,:last,:name,'zoom') ON CONFLICT(attendance_session_id,student_user_id) DO UPDATE SET status=CASE WHEN lms_attendance_records.source='manual_override' THEN lms_attendance_records.status ELSE EXCLUDED.status END,
          attended_seconds=EXCLUDED.attended_seconds,attendance_percentage=EXCLUDED.attendance_percentage,first_join_time=EXCLUDED.first_join_time,last_leave_time=EXCLUDED.last_leave_time,google_participant_name=EXCLUDED.google_participant_name,source=CASE WHEN lms_attendance_records.source='manual_override' THEN lms_attendance_records.source ELSE 'zoom' END"""),
          {"session":session,"student":student["user_id"],"status":status,"seconds":seconds,"percentage":percentage,"first":first_join,"last":last_leave,"name":", ".join(str(p.get("name") or "") for p in entries) or None})
    await db.execute(text("UPDATE lms_online_meetings SET processing_status='attendance_ready',processing_error=NULL WHERE meeting_id=:id"),{"id":meeting_id}); await db.commit()


def _dt(value: str|None) -> datetime|None:
    return datetime.fromisoformat(value.replace("Z","+00:00")) if value else None


async def process_jobs(db: AsyncSession, limit: int=5) -> dict:
    jobs=(await db.execute(text("""UPDATE lms_zoom_jobs SET status='processing',attempts=attempts+1,updated_at=now() WHERE job_id IN
      (SELECT job_id FROM lms_zoom_jobs WHERE status IN ('pending','failed') AND available_at<=now() ORDER BY job_id FOR UPDATE SKIP LOCKED LIMIT :limit)
      RETURNING *"""),{"limit":limit})).mappings().all(); await db.commit(); done=failed=0
    for job in jobs:
        try:
            await db.execute(text("UPDATE lms_online_meetings SET processing_status=:status,processing_error=NULL WHERE meeting_id=:id"),{"status":f"processing_{job['job_type']}","id":job["meeting_id"]})
            await db.commit()
            if job["job_type"]=="attendance":
                owner=await db.scalar(text("SELECT lecturer_user_id FROM lms_online_meetings WHERE meeting_id=:id"),{"id":job["meeting_id"]})
                await sync_attendance(db,job["meeting_id"],owner)
            else: await process_recordings(db,job["meeting_id"],job["payload"])
            await db.execute(text("UPDATE lms_online_meetings SET processing_status=:status,processing_error=NULL WHERE meeting_id=:meeting"),{"status":f"{job['job_type']}_ready","meeting":job["meeting_id"]})
            await db.execute(text("UPDATE lms_zoom_jobs SET status='completed',last_error=NULL,updated_at=now() WHERE job_id=:id"),{"id":job["job_id"]}); await db.commit(); done+=1
        except Exception as exc:
            error=str(exc)[:1000]
            await db.rollback()
            await db.execute(text("UPDATE lms_online_meetings SET processing_status='failed',processing_error=:error WHERE meeting_id=:meeting"),{"error":error,"meeting":job["meeting_id"]})
            await db.execute(text("UPDATE lms_zoom_jobs SET status='failed',last_error=:error,available_at=now()+interval '5 minutes',updated_at=now() WHERE job_id=:id"),{"error":error,"id":job["job_id"]}); await db.commit(); failed+=1
    return {"claimed":len(jobs),"completed":done,"failed":failed}


async def process_recordings(db: AsyncSession, meeting_id: int, payload: dict) -> None:
    # Recording transfer is intentionally queued: the worker survives API restarts and retries safely.
    from app.modules.lms import vimeo_service
    meeting=(await db.execute(text("""SELECT m.*,c.code class_code,c.course_id FROM lms_online_meetings m JOIN lms_classes c ON c.class_id=m.class_id WHERE m.meeting_id=:id"""),{"id":meeting_id})).mappings().first()
    host=(await db.execute(text("SELECT * FROM lms_zoom_host_connections WHERE connection_id=:id"),{"id":meeting["zoom_host_connection_id"]})).mappings().first(); token=await _access_token(db,dict(host))
    download_token=_recording_download_token(payload,token)
    files=payload.get("payload",{}).get("object",{}).get("recording_files",[])
    preferred=_preferred_recording_files(files)
    if not preferred: return
    course=await db.get(__import__('app.modules.lms.models',fromlist=['LmsCourse']).LmsCourse,meeting["course_id"])
    course_folder=await vimeo_service.ensure_course_workspace(db,course)
    async with vimeo_service.VimeoClient() as vimeo:
        folder=await vimeo.ensure_folder(f"Recordings · {meeting['class_code']}",course_folder)
    for part,file in enumerate(preferred,1):
        exists=await db.scalar(text("SELECT 1 FROM lms_zoom_recordings WHERE zoom_recording_file_id=:id AND status IN ('published','deleted')"),{"id":file["id"]})
        if exists: continue
        temp=None
        try:
            fd,temp=tempfile.mkstemp(suffix='.mp4'); os.close(fd)
            async with httpx.AsyncClient(timeout=None,follow_redirects=True) as client:
                download_url=file['download_url']; bearer=download_token
                async def save_download(url: str, access_token: str) -> int:
                    async with client.stream('GET',url,headers={"Authorization":f"Bearer {access_token}"}) as response:
                        if response.is_error:
                            return response.status_code
                        with open(temp,'wb') as output:
                            async for chunk in response.aiter_bytes(1024*1024): output.write(chunk)
                        return response.status_code
                download_status=await save_download(download_url,bearer)
                if download_status==401 and bearer!=token:
                    recording_ref=_recording_api_ref(meeting["provider_meeting_uuid"] or meeting["provider_meeting_id"])
                    refreshed=await client.get(f"{ZOOM_API}/meetings/{recording_ref}/recordings",headers={"Authorization":f"Bearer {token}"})
                    if refreshed.is_error:
                        raise ValidationError(f"Zoom could not refresh the expired recording link (HTTP {refreshed.status_code}).")
                    refreshed_files=refreshed.json().get("recording_files",[])
                    fresh=next((item for item in refreshed_files if str(item.get("id"))==str(file.get("id"))),None)
                    if not fresh or not fresh.get("download_url"):
                        raise ValidationError("Zoom did not return a current download link for this recording file.")
                    download_status=await save_download(fresh["download_url"],token)
                if download_status>=400:
                    raise ValidationError(f"Zoom recording download failed with HTTP {download_status}. Check the connected host recording scope.")
            size=os.path.getsize(temp); title=f"{meeting['title']} · {meeting['start_time'].date()}"+(f" · Part {part}" if len(preferred)>1 else "")
            async with vimeo_service.VimeoClient() as vimeo:
                ticket=await vimeo.create_upload(title,"Automatic Zoom class recording",size)
                offset=0
                async with httpx.AsyncClient(timeout=None) as upload_client:
                    with open(temp,'rb') as source:
                        while chunk:=source.read(8*1024*1024):
                            response=await upload_client.patch(ticket.upload_link,content=chunk,headers={"Tus-Resumable":"1.0.0","Upload-Offset":str(offset),"Content-Type":"application/offset+octet-stream"}); response.raise_for_status(); offset=int(response.headers.get('Upload-Offset',offset+len(chunk)))
                video=await vimeo.get_video(ticket.video_uri); await vimeo.add_video_to_folder(folder,ticket.video_uri)
            resource=str(video.get('link') or '')
            # zoom_published_at starts the cloud retention clock: the file is only
            # removed from Zoom storage once it is safely published to Vimeo.
            await db.execute(text("""INSERT INTO lms_zoom_recordings(meeting_id,zoom_recording_file_id,recording_type,part_number,status,vimeo_video_uri,title,description,resource_url,thumbnail_url,duration_minutes,zoom_published_at)
              VALUES(:m,:file,:type,:part,'published',:vimeo,:title,'Automatic Zoom class recording',:url,:thumb,:duration,now())
              ON CONFLICT(zoom_recording_file_id) DO UPDATE SET status='published',vimeo_video_uri=EXCLUDED.vimeo_video_uri,title=EXCLUDED.title,description=EXCLUDED.description,resource_url=EXCLUDED.resource_url,thumbnail_url=EXCLUDED.thumbnail_url,duration_minutes=EXCLUDED.duration_minutes,learning_item_id=NULL,error=NULL,zoom_published_at=COALESCE(lms_zoom_recordings.zoom_published_at,now())"""),
              {"m":meeting_id,"file":file["id"],"type":file.get("recording_type"),"part":part,"vimeo":ticket.video_uri,"title":title,"url":resource,"thumb":vimeo_service._thumbnail_url(video),"duration":max(1,round(int(video.get('duration') or 0)/60))}); await db.commit()
        finally:
            if temp and os.path.exists(temp): os.unlink(temp)


async def sweep_missing_attendance(db: AsyncSession, now: datetime, limit: int=10) -> int:
    """Queue attendance for finished classes Zoom never reported as ended.

    Zoom webhooks can be lost or misconfigured, which would otherwise leave a
    finished class without attendance until someone pressed sync by hand.
    """
    enabled=await db.scalar(text("SELECT attendance_sync_enabled FROM lms_zoom_settings WHERE settings_id=1"))
    if not enabled: return 0
    # A short grace period lets Zoom's participant report become available.
    rows=(await db.execute(text("""SELECT m.meeting_id FROM lms_online_meetings m
      LEFT JOIN lms_attendance_sessions s ON s.meeting_id=m.meeting_id
      WHERE m.provider='zoom' AND m.status='completed' AND m.end_time < :cutoff
        AND (s.attendance_session_id IS NULL OR s.sync_status<>'synced')
        AND NOT EXISTS (SELECT 1 FROM lms_zoom_jobs j WHERE j.meeting_id=m.meeting_id
          AND j.job_type='attendance' AND j.status IN ('pending','processing'))
      ORDER BY m.end_time DESC LIMIT :limit"""),
      {"cutoff":now-timedelta(minutes=10),"limit":limit})).mappings().all()
    for row in rows:
        await db.execute(text("""INSERT INTO lms_zoom_jobs(event_key,meeting_id,job_type,payload,available_at)
          VALUES(:key,:meeting,'attendance','{}'::jsonb,now()) ON CONFLICT(event_key) DO NOTHING"""),
          {"key":f"sweep:{row['meeting_id']}:attendance","meeting":row["meeting_id"]})
    await db.commit(); return len(rows)


async def purge_expired_cloud_recordings(db: AsyncSession, now: datetime, limit: int=10) -> int:
    """Delete published recordings from Zoom cloud storage after the retention window.

    Zoom cloud storage is a paid quota, and the authoritative copy now lives on
    Vimeo. Only rows that reached 'published' are considered, so a failed upload
    never destroys the sole copy of a class.
    """
    settings_row=(await db.execute(text("SELECT cloud_retention_days FROM lms_zoom_settings WHERE settings_id=1"))).mappings().first()
    retention_days=int((settings_row or {}).get("cloud_retention_days") or 2)
    cutoff=now-timedelta(days=retention_days)
    rows=(await db.execute(text("""SELECT zr.recording_id,zr.zoom_recording_file_id,m.provider_meeting_uuid,m.provider_meeting_id,m.zoom_host_connection_id
      FROM lms_zoom_recordings zr JOIN lms_online_meetings m ON m.meeting_id=zr.meeting_id
      WHERE zr.status='published' AND zr.zoom_cloud_deleted_at IS NULL
        AND zr.zoom_published_at IS NOT NULL AND zr.zoom_published_at <= :cutoff
      ORDER BY zr.zoom_published_at LIMIT :limit"""),{"cutoff":cutoff,"limit":limit})).mappings().all()
    deleted=0
    for row in rows:
        try:
            host=(await db.execute(text("SELECT * FROM lms_zoom_host_connections WHERE connection_id=:id"),{"id":row["zoom_host_connection_id"]})).mappings().first()
            if not host:
                raise ValidationError("The Zoom host account for this recording is no longer connected")
            token=await _access_token(db,dict(host))
            meeting_ref=_recording_api_ref(row["provider_meeting_uuid"] or row["provider_meeting_id"])
            async with httpx.AsyncClient(timeout=30) as client:
                response=await client.delete(
                    f"{ZOOM_API}/meetings/{meeting_ref}/recordings/{row['zoom_recording_file_id']}",
                    params={"action":"delete"},headers={"Authorization":f"Bearer {token}"})
            # 404 means Zoom already removed the file, which satisfies the goal.
            if response.is_error and response.status_code != 404:
                raise ValidationError(f"Zoom refused the recording delete (HTTP {response.status_code})")
            await db.execute(text("""UPDATE lms_zoom_recordings
              SET zoom_cloud_deleted_at=now(),zoom_cloud_delete_error=NULL,updated_at=now() WHERE recording_id=:id"""),{"id":row["recording_id"]})
            await db.commit(); deleted+=1
        except Exception as exc:
            await db.rollback()
            await db.execute(text("""UPDATE lms_zoom_recordings
              SET zoom_cloud_delete_error=:error,updated_at=now() WHERE recording_id=:id"""),
              {"error":str(exc)[:500],"id":row["recording_id"]})
            await db.commit()
    return deleted


async def list_class_recordings(db: AsyncSession, class_id: int, user_id: int, role: str) -> list[dict]:
    from app.modules.lms import content_service

    class_row=(await db.execute(text("SELECT class_id,course_id FROM lms_classes WHERE class_id=:id"),{"id":class_id})).mappings().first()
    if not class_row: raise NotFoundError(f"Class {class_id} not found")
    if role in {"SUPER_ADMIN","ADMIN","LECTURER"}:
        await content_service.ensure_course_manager(db,class_row["course_id"],user_id)
    elif role=="STUDENT":
        enrolled=await db.scalar(text("SELECT 1 FROM lms_class_students WHERE class_id=:class_id AND student_user_id=:user_id"),{"class_id":class_id,"user_id":user_id})
        if not enrolled: raise ForbiddenError("You are not enrolled in this class")
    else: raise ForbiddenError("This LMS role cannot view class recordings")
    rows=(await db.execute(text("""SELECT zr.recording_id,zr.meeting_id,zr.title,zr.description,zr.resource_url,zr.thumbnail_url,zr.duration_minutes,
      zr.recording_type,zr.part_number,m.title meeting_title,m.start_time
      FROM lms_zoom_recordings zr JOIN lms_online_meetings m ON m.meeting_id=zr.meeting_id
      WHERE m.class_id=:class_id AND zr.status='published' AND zr.resource_url IS NOT NULL
      ORDER BY m.start_time DESC,zr.part_number"""),{"class_id":class_id})).mappings().all()
    preferred_types: dict[int, str] = {}
    for row in rows:
        meeting_id = int(row["meeting_id"])
        recording_type = str(row["recording_type"] or "")
        current = preferred_types.get(meeting_id)
        if current is None or _recording_type_priority(recording_type) < _recording_type_priority(current):
            preferred_types[meeting_id] = recording_type
    return [
        dict(row) for row in rows
        if str(row["recording_type"] or "") == preferred_types[int(row["meeting_id"])]
    ]


async def delete_class_recording(db: AsyncSession, class_id: int, recording_id: int, user_id: int) -> None:
    from app.modules.lms import content_service
    from app.modules.lms.repository import ContentRepository

    row=(await db.execute(text("""SELECT zr.*,m.class_id,c.course_id FROM lms_zoom_recordings zr
      JOIN lms_online_meetings m ON m.meeting_id=zr.meeting_id JOIN lms_classes c ON c.class_id=m.class_id
      WHERE zr.recording_id=:recording_id AND m.class_id=:class_id AND zr.status='published'"""),
      {"recording_id":recording_id,"class_id":class_id})).mappings().first()
    if not row: raise NotFoundError("Class recording not found")
    await content_service.ensure_course_manager(db,row["course_id"],user_id)
    await db.execute(text("UPDATE lms_zoom_recordings SET status='deleted',learning_item_id=NULL,updated_at=now() WHERE recording_id=:id"),{"id":recording_id})
    await db.commit()
    video_uri=row["vimeo_video_uri"] or vimeo_service.video_uri_from_url(row["resource_url"])
    if video_uri:
        try:
            async with vimeo_service.VimeoClient() as vimeo:
                await vimeo.delete_video(video_uri)
        except Exception:
            logger.warning("Vimeo cleanup failed for deleted Zoom recording %s",recording_id,exc_info=True)
    if row["learning_item_id"]:
        item=await ContentRepository(db).get_item(row["learning_item_id"])
        if item: await ContentRepository(db).delete_item(item)
