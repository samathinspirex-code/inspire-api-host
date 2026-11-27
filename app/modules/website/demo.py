"""Free demo LMS accounts for the new website: sign up, log in, reset (no verification by design: sample data that
expires after DEMO_TTL_HOURS), saved portal state, the Live Campus feed and the demo classes students see.

Sessions are opaque random tokens (only their SHA-256 is stored), separate from CMS/LMS logins.
"""
import hashlib
import hmac
import json
import re
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import APIError, ConflictError, NotFoundError, UnauthorizedError, ValidationError

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
NAME_BAD = re.compile(r"[^A-Za-zÀ-ɏ .\-]")
MAX_STATE_BYTES = 600_000
ITEM_TYPES = {"video", "pdf", "pptx", "text", "link", "assignment", "quiz"}


def now() -> datetime:
    return datetime.now(timezone.utc)


def clean_name(raw: str) -> str:
    return re.sub(r"\s+", " ", NAME_BAD.sub("", raw or "")).strip()[:80]


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$" + salt.hex() + "$" + digest.hex()


def check_password(password: str, stored: str) -> bool:
    try:
        kind, salt, digest = stored.split("$")
        if kind != "scrypt":
            return False
        test = hashlib.scrypt((password or "").encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(test.hex(), digest)
    except (ValueError, TypeError):
        return False


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def _session(db: AsyncSession, user: dict) -> dict:
    token = secrets.token_urlsafe(32)
    await db.execute(text("INSERT INTO web_demo_sessions(token_hash, user_id, expires_at) VALUES (:h, :u, :e)"),
                     {"h": _hash_token(token), "u": user["id"], "e": user["expires_at"]})
    await db.commit()
    return {"token": token, "expires_at": user["expires_at"].isoformat(), "user": {"name": user["full_name"], "email": user["email"]}}


async def _user_by_email(db: AsyncSession, email: str) -> dict | None:
    r = (await db.execute(text("SELECT * FROM web_demo_users WHERE email=:e"), {"e": email})).mappings().first()
    return dict(r) if r else None


async def purge_user(db: AsyncSession, user_id) -> None:
    await db.execute(text("DELETE FROM web_demo_users WHERE id=:u"), {"u": user_id})


async def signup(db: AsyncSession, *, name: str, email: str, phone: str, interest: str, password: str) -> tuple[dict, dict]:
    name, email = clean_name(name), (email or "").strip().lower()
    digits = re.sub(r"\D", "", phone or "")
    if len(name) < 2 or not EMAIL_RE.match(email) or len(digits) < 9 or len(password or "") < 6:
        raise ValidationError("Please add your name, a valid email, a mobile number and a password of at least 6 characters.")
    existing = await _user_by_email(db, email)
    if existing:
        if existing["expires_at"] > now():
            raise ConflictError("This email already has a demo account. Log in instead.")
        await purge_user(db, existing["id"])
    expires = now() + timedelta(hours=settings.DEMO_TTL_HOURS)
    r = (await db.execute(text("""INSERT INTO web_demo_users(email, full_name, phone, interest, password_hash, expires_at)
        VALUES (:e, :n, :p, :i, :h, :x) RETURNING *"""), {"e": email, "n": name, "p": (phone or "").strip()[:40], "i": (interest or "")[:80],
                                                          "h": hash_password(password), "x": expires})).mappings().first()
    user = dict(r)
    await db.execute(text("INSERT INTO web_demo_states(user_id, state) VALUES (:u, '{}'::jsonb)"), {"u": user["id"]})
    await db.commit()
    return user, await _session(db, user)


async def login(db: AsyncSession, email: str, password: str) -> dict:
    user = await _user_by_email(db, (email or "").strip().lower())
    if not user or user["expires_at"] <= now() or not check_password(password or "", user["password_hash"]):
        raise UnauthorizedError("That email and password don't match an active demo account.")
    return await _session(db, user)


async def reset_password(db: AsyncSession, email: str, password: str) -> dict:
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email):
        raise ValidationError("Please enter the email you used for your demo account.")
    if len(password or "") < 6:
        raise ValidationError("Choose a password of at least 6 characters.")
    user = await _user_by_email(db, email)
    if not user or user["expires_at"] <= now():
        raise NotFoundError("There's no demo account with this email yet. Create a free account below.")
    await db.execute(text("UPDATE web_demo_users SET password_hash=:h WHERE id=:u"), {"h": hash_password(password), "u": user["id"]})
    await db.execute(text("DELETE FROM web_demo_sessions WHERE user_id=:u"), {"u": user["id"]})
    return await _session(db, user)


async def user_for_token(db: AsyncSession, token: str) -> dict:
    if not token or len(token) > 200:
        raise UnauthorizedError("Demo session expired. Please log in again.")
    r = (await db.execute(text("""SELECT u.* FROM web_demo_sessions s JOIN web_demo_users u ON u.id=s.user_id
        WHERE s.token_hash=:h AND s.expires_at > now() AND u.expires_at > now()"""), {"h": _hash_token(token)})).mappings().first()
    if not r:
        raise UnauthorizedError("Demo session expired. Please log in again.")
    return dict(r)


def _ledger(state) -> list:
    led = (state or {}).get("ledger") if isinstance(state, dict) else None
    return led if isinstance(led, list) else []


async def save_state(db: AsyncSession, user: dict, state: dict, raw_size: int) -> int:
    """Store the portal state; new points-ledger lines become Live Campus events (first name + initial only)."""
    if raw_size > MAX_STATE_BYTES:
        raise APIError(413, "DEMO_STATE_TOO_LARGE", "Demo data is too large.")
    old = await db.scalar(text("SELECT state FROM web_demo_states WHERE user_id=:u"), {"u": user["id"]})
    first_save = not old
    before = {(e.get("d"), e.get("t"), e.get("p")) for e in _ledger(old) if isinstance(e, dict)}
    parts = (user["full_name"] or "Student").split()
    who = parts[0] + (" " + parts[-1][0] + "." if len(parts) > 1 else "")
    events = 0
    for e in _ledger(state):
        if not isinstance(e, dict) or first_save or (e.get("d"), e.get("t"), e.get("p")) in before:
            continue
        try:
            pts = int(e.get("p") or 0)
        except (TypeError, ValueError):
            pts = 0
        if pts > 0 and events < 20:
            await db.execute(text("INSERT INTO web_demo_events(user_id, who, label, points) VALUES (:u, :w, :l, :p)"),
                             {"u": user["id"], "w": who, "l": str(e.get("t") or "Activity")[:200], "p": min(pts, 100000)})
            events += 1
    await db.execute(text("""INSERT INTO web_demo_states(user_id, state, updated_at) VALUES (:u, CAST(:s AS jsonb), now())
        ON CONFLICT (user_id) DO UPDATE SET state=EXCLUDED.state, updated_at=now()"""), {"u": user["id"], "s": json.dumps(state)})
    await db.execute(text("UPDATE web_demo_users SET last_seen_at=now() WHERE id=:u"), {"u": user["id"]})
    await db.commit()
    return events


async def purge_expired(db: AsyncSession) -> int:
    n = (await db.execute(text("DELETE FROM web_demo_users WHERE expires_at <= now()"))).rowcount or 0
    await db.execute(text("DELETE FROM web_demo_sessions WHERE expires_at <= now()"))
    await db.execute(text("DELETE FROM web_demo_events WHERE created_at < now() - interval '7 days'"))
    await db.commit()
    return n


async def live_feed(db: AsyncSession) -> dict:
    rows = (await db.execute(text("""SELECT who, label, points, created_at FROM web_demo_events
        WHERE created_at >= now() - interval '6 hours' ORDER BY created_at DESC LIMIT 20"""))).mappings().all()
    active = await db.scalar(text("SELECT COUNT(*) FROM web_demo_users WHERE last_seen_at >= now() - interval '30 minutes'"))
    accounts = await db.scalar(text("SELECT COUNT(*) FROM web_demo_users WHERE expires_at > now()"))
    return {"now": now().isoformat(), "active_now": int(active or 0), "demo_accounts": int(accounts or 0),
            "events": [{"who": r["who"], "label": r["label"], "points": r["points"], "at": r["created_at"].isoformat()} for r in rows]}


# ---------------------------------------------------------------- demo classes
def class_dict(c: dict, sections: list | None = None) -> dict:
    out = {k: c[k] for k in ("id", "code", "title", "awarding_body", "school", "programme", "intake", "study_mode", "status", "image",
                             "description", "sort_order", "published")}
    out["start_date"] = c["start_date"].isoformat() if c.get("start_date") else None
    out["end_date"] = c["end_date"].isoformat() if c.get("end_date") else None
    out["lecturers_text"], out["takeaways_text"] = c["lecturers"], c["takeaways"]
    out["lecturers"] = [x.strip() for x in (c["lecturers"] or "").split(",") if x.strip()]
    out["takeaways"] = [x.strip() for x in (c["takeaways"] or "").splitlines() if x.strip()]
    if sections is not None:
        out["sections"] = sections
    return out


async def class_tree(db: AsyncSession, class_id: int, published_only: bool = False) -> list[dict]:
    secs = (await db.execute(text(f"""SELECT * FROM web_demo_class_sections WHERE class_id=:c {"AND status='active'" if published_only else ""}
        ORDER BY position, id"""), {"c": class_id})).mappings().all()
    if not secs:
        return []
    items = (await db.execute(text(f"""SELECT * FROM web_demo_class_items WHERE section_id = ANY(:ids) {"AND status='published'" if published_only else ""}
        ORDER BY position, id"""), {"ids": [s["id"] for s in secs]})).mappings().all()
    by: dict = {}
    for i in items:
        by.setdefault(i["section_id"], []).append({k: i[k] for k in ("id", "item_type", "title", "description", "resource_url", "text_content",
                                                                    "duration_minutes", "status", "is_required", "position")})
    return [{"id": s["id"], "title": s["title"], "description": s["description"], "status": s["status"], "position": s["position"],
             "items": by.get(s["id"], [])} for s in secs]


async def published_classes(db: AsyncSession) -> list[dict]:
    rows = (await db.execute(text("SELECT * FROM web_demo_classes WHERE published='published' ORDER BY sort_order, id"))).mappings().all()
    return [class_dict(dict(c), await class_tree(db, c["id"], published_only=True)) for c in rows]


CLASS_FIELDS = ("code", "title", "awarding_body", "school", "programme", "intake", "study_mode", "start_date", "end_date", "lecturers",
                "status", "image", "description", "takeaways", "sort_order", "published")


def clean_class(body: dict) -> dict:
    out = {}
    for k in CLASS_FIELDS:
        if k not in body:
            continue
        v = body[k]
        if k in ("start_date", "end_date"):
            if v in (None, ""):
                v = None
            else:
                try:
                    v = datetime.fromisoformat(str(v)[:10]).date()
                except ValueError as exc:
                    raise ValidationError(f"Use a valid date for {k.replace('_', ' ')}") from exc
        elif k == "sort_order":
            try:
                v = int(v or 0)
            except (TypeError, ValueError) as exc:
                raise ValidationError("Order must be a number") from exc
        elif k == "status" and v not in ("Planned", "Active", "Completed"):
            raise ValidationError("Class status must be Planned, Active or Completed")
        elif k == "published" and v not in ("published", "draft"):
            raise ValidationError("Choose published or draft")
        elif isinstance(v, str):
            v = v.strip()[:4000]
        elif v is None:
            v = ""
        out[k] = v
    if "title" in out and not out["title"]:
        raise ValidationError("Give the class a title")
    return out


def clean_item(body: dict) -> dict:
    t = body.get("item_type", "video")
    if t not in ITEM_TYPES:
        raise ValidationError("Unknown content type")
    title = str(body.get("title") or "").strip()
    if not title:
        raise ValidationError("Give the learning item a title")
    url = str(body.get("resource_url") or "").strip()[:800]
    if url and not url.startswith(("https://", "/")):
        raise ValidationError("Links must start with https://")
    dur = body.get("duration_minutes")
    try:
        dur = int(dur) if dur not in (None, "") and int(dur) > 0 else None
    except (TypeError, ValueError):
        dur = None
    return {"item_type": t, "title": title[:200], "description": str(body.get("description") or "")[:4000], "resource_url": url,
            "text_content": str(body.get("text_content") or "")[:20000], "duration_minutes": dur,
            "status": "draft" if body.get("status") == "draft" else "published", "is_required": bool(body.get("is_required"))}
