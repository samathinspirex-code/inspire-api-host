from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.modules.insights.demo_data import GTM_EVENTS, PLATFORMS


async def catalogue(db: AsyncSession) -> dict:
    accounts = (await db.execute(text(
        "SELECT connector_id, platform, name, account_id, owner, status, created_at FROM connectors ORDER BY created_at DESC"
    ))).mappings().all()
    lines = (await db.execute(text(
        "SELECT w.line_id, w.counsellor_user_id, w.phone_number, w.display_name, w.phone_number_id, "
        "coalesce(u.full_name, u.email) "
        "FROM whatsapp_lines w LEFT JOIN users u ON u.user_id = w.counsellor_user_id ORDER BY w.created_at DESC"
    ))).all()
    counsellors = (await db.execute(text(
        "SELECT DISTINCT u.user_id, coalesce(nullif(u.full_name, ''), u.email) FROM users u "
        "JOIN user_access_levels ual ON ual.user_id = u.user_id "
        "JOIN access_levels al ON al.access_level_id = ual.access_level_id AND al.access_key = 'COUNSELLOR' "
        "WHERE u.is_active = true ORDER BY 2"
    ))).all()
    return {
        "platforms": PLATFORMS,
        "events": GTM_EVENTS,
        "accounts": [dict(row) | {"created_at": row["created_at"].isoformat() if row["created_at"] else None, "source": "cms"} for row in accounts],
        "whatsapp_lines": [
            {"id": row[0], "counsellor_user_id": row[1], "number": row[2], "display": row[3], "phone_number_id": row[4], "counsellor": row[5], "source": "cms"}
            for row in lines
        ],
        "counsellors": [{"user_id": row[0], "name": row[1]} for row in counsellors],
    }


async def add_account(db: AsyncSession, payload: dict, user_id: int) -> dict:
    known = {item["id"] for item in PLATFORMS}
    if payload["platform"] not in known:
        raise ValidationError("Unknown platform")
    row = (await db.execute(text(
        "INSERT INTO connectors (platform, name, account_id, owner, created_by) "
        "VALUES (:platform, :name, :account, :owner, :user) RETURNING connector_id"
    ), {**payload, "user": user_id})).one()
    await db.commit()
    return {"id": row[0]}


async def delete_account(db: AsyncSession, connector_id: int) -> None:
    result = await db.execute(text("DELETE FROM connectors WHERE connector_id = :id"), {"id": connector_id})
    await db.commit()
    if result.rowcount == 0:
        raise NotFoundError("Connector not found")


async def add_line(db: AsyncSession, payload: dict, user_id: int) -> dict:
    number = (payload.get("number") or "").strip()
    if len(number) < 6:
        raise ValidationError("Enter the WhatsApp number")
    row = (await db.execute(text(
        "INSERT INTO whatsapp_lines (counsellor_user_id, phone_number, display_name, phone_number_id, created_by) "
        "VALUES (:counsellor, :number, :display, :pid, :user) RETURNING line_id"
    ), {
        "counsellor": payload.get("counsellor_user_id"),
        "number": number,
        "display": payload.get("display") or None,
        "pid": payload.get("phone_number_id") or None,
        "user": user_id,
    })).one()
    await db.commit()
    return {"id": row[0]}


async def delete_line(db: AsyncSession, line_id: int) -> None:
    result = await db.execute(text("DELETE FROM whatsapp_lines WHERE line_id = :id"), {"id": line_id})
    await db.commit()
    if result.rowcount == 0:
        raise NotFoundError("WhatsApp line not found")
