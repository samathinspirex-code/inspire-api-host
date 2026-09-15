"""Zoom host-pool, embedded joining, attendance, and Vimeo recording processing."""
import asyncio
import base64
import hashlib
import hmac
import json
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
            raise ValidationError("Zoom could not complete the account connection")
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
    if host is None: raise ValidationError("All Zoom host accounts are at capacity for this time. Choose another time or connect another host.")
    return host


async def create_zoom_meeting(db: AsyncSession, payload, lecturer_id: int, class_, course) -> dict:
    zoom_settings=(await db.execute(text("SELECT * FROM lms_zoom_settings WHERE settings_id=1"))).mappings().first()
    if not zoom_settings or not zoom_settings["enabled"]: raise ValidationError("Zoom integration is not enabled")
    host=await _claim_host(db,lecturer_id,payload.start_time,payload.end_time); token=await _access_token(db,host)
    duration=max(1,round((payload.end_time-payload.start_time).total_seconds()/60))
    passcode=secrets.token_hex(4)
    body={"topic":payload.title.strip(),"type":2,"start_time":payload.start_time.isoformat(),"duration":duration,"password":passcode,
      "timezone":class_.timezone,"agenda":(payload.description or "").strip(),
      "settings":{"registration_type":1,"approval_type":0,"waiting_room":True,"join_before_host":False,
        "meeting_authentication":False,"auto_recording":"cloud" if zoom_settings["automatic_recording"] else "none"}}
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
        raise ValidationError("This Zoom host is already at capacity for the new time. Choose another time.")
    token=await _access_token(db,dict(host))
    body={"topic":payload.title.strip(),"start_time":payload.start_time.isoformat(),
      "duration":max(1,round((payload.end_time-payload.start_time).total_seconds()/60)),
      "timezone":meeting.timezone,"agenda":(payload.description or "").strip()}
    async with httpx.AsyncClient(timeout=25) as client:
        response=await client.patch(f"{ZOOM_API}/meetings/{meeting.provider_meeting_id}",headers={"Authorization":f"Bearer {token}"},json=body)
    if response.is_error:
        raise ValidationError("Zoom could not update this meeting. Check the host connection and try again.")
    return {"title":payload.title.strip(),"description":(payload.description or "").strip() or None,
      "start_time":payload.start_time,"end_time":payload.end_time,"processing_error":None}


async def cancel_zoom_meeting(db: AsyncSession, meeting) -> None:
    host=(await db.execute(text("SELECT * FROM lms_zoom_host_connections WHERE connection_id=:id"),{"id":meeting.zoom_host_connection_id})).mappings().first()
    if not host:
        raise ValidationError("The allocated Zoom host connection no longer exists")
    token=await _access_token(db,dict(host))
    async with httpx.AsyncClient(timeout=25) as client:
        response=await client.delete(f"{ZOOM_API}/meetings/{meeting.provider_meeting_id}",headers={"Authorization":f"Bearer {token}"})
    if response.is_error and response.status_code != 404:
        raise ValidationError("Zoom could not cancel this meeting. Try again.")


async def register_class_students(db: AsyncSession, meeting_id: int) -> None:
    student_ids=(await db.execute(text("""SELECT cs.student_user_id FROM lms_online_meetings m
      JOIN lms_class_students cs ON cs.class_id=m.class_id WHERE m.meeting_id=:id"""),{"id":meeting_id})).scalars().all()
    for student_id in student_ids:
        try:
            await register_student(db,meeting_id,student_id)
        except Exception:
            # Registration is retried lazily when the student opens the meeting.
            # A single stale student profile must not orphan the scheduled meeting.
            await db.rollback()


async def register_student(db: AsyncSession, meeting_id: int, student_id: int) -> dict:
    existing=(await db.execute(text("SELECT * FROM lms_zoom_registrations WHERE meeting_id=:m AND student_user_id=:s"),{"m":meeting_id,"s":student_id})).mappings().first()
    if existing: return dict(existing)
    row=(await db.execute(text("""SELECT m.provider_meeting_id,m.zoom_host_connection_id,u.email,u.full_name
      FROM lms_online_meetings m JOIN lms_class_students cs ON cs.class_id=m.class_id
      JOIN users u ON u.user_id=cs.student_user_id WHERE m.meeting_id=:m AND cs.student_user_id=:s AND u.is_active"""),{"m":meeting_id,"s":student_id})).mappings().first()
    if not row: raise ForbiddenError("You are not enrolled in this meeting's class")
    host=(await db.execute(text("SELECT * FROM lms_zoom_host_connections WHERE connection_id=:id"),{"id":row["zoom_host_connection_id"]})).mappings().first(); token=await _access_token(db,dict(host))
    names=(row["full_name"] or "Student").strip().split(" ",1)
    async with httpx.AsyncClient(timeout=20) as client:
        response=await client.post(f"{ZOOM_API}/meetings/{row['provider_meeting_id']}/registrants",headers={"Authorization":f"Bearer {token}"},json={"email":row["email"],"first_name":names[0],"last_name":names[1] if len(names)>1 else "Student"})
    if response.is_error: raise ValidationError("Zoom could not register this student for the meeting")
    data=response.json(); join_token=data.get("tk")
    await db.execute(text("""INSERT INTO lms_zoom_registrations(meeting_id,student_user_id,zoom_registrant_id,encrypted_join_token,join_url)
      VALUES(:m,:s,:r,:t,:url) ON CONFLICT(meeting_id,student_user_id) DO UPDATE SET zoom_registrant_id=EXCLUDED.zoom_registrant_id,
      encrypted_join_token=EXCLUDED.encrypted_join_token,join_url=EXCLUDED.join_url"""),{"m":meeting_id,"s":student_id,"r":data.get("registrant_id"),"t":_encrypt(join_token) if join_token else None,"url":data["join_url"]})
    await db.commit(); return {"join_url":data["join_url"],"join_token":join_token}


def _sdk_signature(meeting_number: str, role: int) -> str:
    if not settings.ZOOM_MEETING_SDK_KEY or not settings.ZOOM_MEETING_SDK_SECRET: raise ValidationError("Zoom Meeting SDK credentials are not configured")
    now=int(datetime.now(timezone.utc).timestamp())-30; exp=now+7200
    header={"alg":"HS256","typ":"JWT"}; payload={"sdkKey":settings.ZOOM_MEETING_SDK_KEY,"mn":meeting_number,"role":role,"iat":now,"exp":exp,"appKey":settings.ZOOM_MEETING_SDK_KEY,"tokenExp":exp}
    enc=lambda value: base64.urlsafe_b64encode(json.dumps(value,separators=(',',':')).encode()).decode().rstrip('=')
    unsigned=f"{enc(header)}.{enc(payload)}"; sig=base64.urlsafe_b64encode(hmac.new(settings.ZOOM_MEETING_SDK_SECRET.encode(),unsigned.encode(),hashlib.sha256).digest()).decode().rstrip('=')
    return f"{unsigned}.{sig}"


async def join_config(db: AsyncSession, meeting_id: int, user_id: int, role: str) -> dict:
    meeting=(await db.execute(text("SELECT * FROM lms_online_meetings WHERE meeting_id=:id AND provider='zoom'"),{"id":meeting_id})).mappings().first()
    if not meeting: raise NotFoundError("Zoom meeting not found")
    is_host=role in {"SUPER_ADMIN","ADMIN"} or (role=="LECTURER" and meeting["lecturer_user_id"]==user_id)
    if not is_host:
        allowed=await db.scalar(text("SELECT 1 FROM lms_class_students WHERE class_id=:c AND student_user_id=:u"),{"c":meeting["class_id"],"u":user_id})
        if not allowed: raise ForbiddenError("You are not enrolled in this meeting's class")
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
    else:
        registration=await register_student(db,meeting_id,user_id); result["registrant_token"]=registration.get("join_token")
    return result


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
    if name=="meeting.ended": kinds.append("attendance")
    if name=="recording.completed": kinds.append("recording")
    for kind in kinds:
        key=f"{name}:{obj.get('uuid') or meeting_id}:{kind}"
        await db.execute(text("""INSERT INTO lms_zoom_jobs(event_key,meeting_id,job_type,payload,available_at)
          VALUES(:key,:meeting,:kind,CAST(:payload AS jsonb),now()+interval '2 minutes') ON CONFLICT(event_key) DO NOTHING"""),
          {"key":key,"meeting":local,"kind":kind,"payload":json.dumps(event)})
    await db.commit()


async def sync_attendance(db: AsyncSession, meeting_id: int, synced_by: int) -> None:
    context=(await db.execute(text("""SELECT m.*,s.attendance_threshold_percentage FROM lms_online_meetings m
      JOIN lms_zoom_settings s ON s.settings_id=1 WHERE m.meeting_id=:id AND m.provider='zoom'"""),{"id":meeting_id})).mappings().first()
    if not context: raise NotFoundError("Zoom meeting not found")
    host=(await db.execute(text("SELECT * FROM lms_zoom_host_connections WHERE connection_id=:id"),{"id":context["zoom_host_connection_id"]})).mappings().first(); token=await _access_token(db,dict(host))
    meeting_ref=quote(context["provider_meeting_uuid"] or context["provider_meeting_id"],safe="")
    async with httpx.AsyncClient(timeout=30) as client:
        response=await client.get(f"{ZOOM_API}/past_meetings/{meeting_ref}/participants",params={"page_size":300},headers={"Authorization":f"Bearer {token}"})
    if response.is_error: raise ValidationError("Zoom attendance is still processing. Retry in a few minutes.")
    participants=response.json().get("participants",[]); actual_start=min((_dt(p.get("join_time")) for p in participants if p.get("join_time")),default=context["start_time"]); actual_end=max((_dt(p.get("leave_time")) for p in participants if p.get("leave_time")),default=context["end_time"]); window=max(1,int((actual_end-actual_start).total_seconds()))
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
    for student in roster:
        entries=buckets[student["email"]]; seconds=sum(int(p.get("duration") or 0) for p in entries); percentage=min(100,round(seconds*100/window,2)); status="present" if percentage>=context["attendance_threshold_percentage"] else "absent"
        joins=[_dt(p.get("join_time")) for p in entries if p.get("join_time")]; leaves=[_dt(p.get("leave_time")) for p in entries if p.get("leave_time")]
        await db.execute(text("""INSERT INTO lms_attendance_records(attendance_session_id,student_user_id,status,attended_seconds,attendance_percentage,first_join_time,last_leave_time,google_participant_name,source)
          VALUES(:session,:student,:status,:seconds,:percentage,:first,:last,:name,'zoom') ON CONFLICT(attendance_session_id,student_user_id) DO UPDATE SET status=CASE WHEN lms_attendance_records.source='manual_override' THEN lms_attendance_records.status ELSE EXCLUDED.status END,
          attended_seconds=EXCLUDED.attended_seconds,attendance_percentage=EXCLUDED.attendance_percentage,first_join_time=EXCLUDED.first_join_time,last_leave_time=EXCLUDED.last_leave_time,google_participant_name=EXCLUDED.google_participant_name,source=CASE WHEN lms_attendance_records.source='manual_override' THEN lms_attendance_records.source ELSE 'zoom' END"""),
          {"session":session,"student":student["user_id"],"status":status,"seconds":seconds,"percentage":percentage,"first":min(joins) if joins else None,"last":max(leaves) if leaves else None,"name":", ".join(str(p.get("name") or "") for p in entries) or None})
    await db.execute(text("UPDATE lms_online_meetings SET status='completed',processing_status='attendance_ready',processing_error=NULL WHERE meeting_id=:id"),{"id":meeting_id}); await db.commit()


def _dt(value: str|None) -> datetime:
    return datetime.fromisoformat(value.replace("Z","+00:00")) if value else datetime.now(timezone.utc)


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
    preferred=[f for f in files if f.get("file_type")=="MP4"]
    order={"shared_screen_with_speaker_view":0,"gallery_view":1,"active_speaker":2}; preferred.sort(key=lambda f:order.get(f.get("recording_type"),9))
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
            await db.execute(text("""INSERT INTO lms_zoom_recordings(meeting_id,zoom_recording_file_id,recording_type,part_number,status,vimeo_video_uri,title,description,resource_url,thumbnail_url,duration_minutes)
              VALUES(:m,:file,:type,:part,'published',:vimeo,:title,'Automatic Zoom class recording',:url,:thumb,:duration)
              ON CONFLICT(zoom_recording_file_id) DO UPDATE SET status='published',vimeo_video_uri=EXCLUDED.vimeo_video_uri,title=EXCLUDED.title,description=EXCLUDED.description,resource_url=EXCLUDED.resource_url,thumbnail_url=EXCLUDED.thumbnail_url,duration_minutes=EXCLUDED.duration_minutes,learning_item_id=NULL,error=NULL"""),
              {"m":meeting_id,"file":file["id"],"type":file.get("recording_type"),"part":part,"vimeo":ticket.video_uri,"title":title,"url":resource,"thumb":vimeo_service._thumbnail_url(video),"duration":max(1,round(int(video.get('duration') or 0)/60))}); await db.commit()
        finally:
            if temp and os.path.exists(temp): os.unlink(temp)


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
    rows=(await db.execute(text("""SELECT zr.recording_id,zr.title,zr.description,zr.resource_url,zr.thumbnail_url,zr.duration_minutes,
      zr.recording_type,zr.part_number,m.title meeting_title,m.start_time
      FROM lms_zoom_recordings zr JOIN lms_online_meetings m ON m.meeting_id=zr.meeting_id
      WHERE m.class_id=:class_id AND zr.status='published' AND zr.resource_url IS NOT NULL
      ORDER BY m.start_time DESC,zr.part_number"""),{"class_id":class_id})).mappings().all()
    return [dict(row) for row in rows]


async def delete_class_recording(db: AsyncSession, class_id: int, recording_id: int, user_id: int) -> None:
    from app.modules.lms import content_service

    row=(await db.execute(text("""SELECT zr.*,m.class_id,c.course_id FROM lms_zoom_recordings zr
      JOIN lms_online_meetings m ON m.meeting_id=zr.meeting_id JOIN lms_classes c ON c.class_id=m.class_id
      WHERE zr.recording_id=:recording_id AND m.class_id=:class_id AND zr.status='published'"""),
      {"recording_id":recording_id,"class_id":class_id})).mappings().first()
    if not row: raise NotFoundError("Class recording not found")
    await content_service.ensure_course_manager(db,row["course_id"],user_id)
    if row["learning_item_id"]:
        await content_service.delete_learning_item(db,row["learning_item_id"],user_id)
    elif row["vimeo_video_uri"]:
        async with vimeo_service.VimeoClient() as vimeo:
            try: await vimeo.delete_video(row["vimeo_video_uri"])
            except vimeo_service.VimeoPermissionError: pass
    await db.execute(text("UPDATE lms_zoom_recordings SET status='deleted',learning_item_id=NULL,updated_at=now() WHERE recording_id=:id"),{"id":recording_id})
    await db.commit()
