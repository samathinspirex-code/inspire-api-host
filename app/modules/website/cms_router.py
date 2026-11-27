"""CMS routes for the new website.

/api/v1/cms/website/...        course listings, FAQs, offers, website settings, demo classes, demo accounts (CMS access;
                                fee plans and settings need CMS_ADMIN)
/api/v1/cms/payments/web/...   website orders, bank slips, plans, Year 2 (Payments access, same rules as Accounts)
"""
import base64
import json

from fastapi import APIRouter, Body, Depends, Query, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.modules.auth.dependencies import get_current_user, require_access
from app.modules.auth.schemas import CurrentUser
from app.modules.payments.db import rows
from app.modules.payments.router import database as payments_db, finance, once
from . import checkout, crm_sync, demo, service
from .detail import LEVELS

router = APIRouter(prefix="/api/v1/cms/website", tags=["cms-website"], dependencies=[Depends(require_access("CMS"))])
pay_router = APIRouter(prefix="/api/v1/cms/payments/web", tags=["Payments"], dependencies=[Depends(finance)])


def cms_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if "CMS_ADMIN" not in user.access:
        raise ForbiddenError("Requires 'CMS_ADMIN' access")
    return user


# ---------------------------------------------------------------- course listings
LISTING_FIELDS = {"design_key", "plan_type", "programme_code", "short_name", "level", "school", "body_label", "card_type", "keywords",
                  "monthly_label", "fee_label", "fee_override", "cpd_addon_fee", "auto_instalments", "featured", "sort_order", "web_status",
                  "detail", "seo_title", "seo_description"}


@router.get("/courses")
async def list_courses(db: AsyncSession = Depends(get_db)):
    return {"data": await service.courses(db, include_hidden=True)}


@router.get("/courses/{course_id}")
async def get_listing(course_id: int, db: AsyncSession = Depends(get_db)):
    item = next((c for c in await service.courses(db, include_hidden=True) if c["course_id"] == course_id), None)
    if not item:
        raise NotFoundError("Course not found or not active")
    stored = (await service.website_rows(db)).get(course_id) or {}
    return {"data": {"course": item, "listing": service.jsonable({k: stored.get(k) for k in LISTING_FIELDS})}}


@router.put("/courses/{course_id}")
async def save_listing(course_id: int, body: dict = Body(...), user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if not await db.scalar(text("SELECT 1 FROM academic_courses WHERE course_id=:c"), {"c": course_id}):
        raise NotFoundError("Course not found")
    data = {k: v for k, v in body.items() if k in LISTING_FIELDS}
    money_fields = {"fee_override", "cpd_addon_fee"}
    if ("fee_override" in data or "plan_type" in data) and "CMS_ADMIN" not in user.access:
        raise ForbiddenError("Only CMS admins can change prices or the fee plan type")
    for k in money_fields & data.keys():
        if data[k] in ("", None):
            data[k] = None
        else:
            try:
                data[k] = float(data[k])
            except (TypeError, ValueError) as exc:
                raise ValidationError("Enter a valid amount") from exc
            if not 0 < data[k] <= 100_000_000:
                raise ValidationError("Enter a valid amount")
    if "plan_type" in data and data["plan_type"] not in ("hnd", "fnd", "pg", "cpd"):
        raise ValidationError("Fee plan type must be hnd, fnd, pg or cpd")
    if "level" in data and data["level"] not in LEVELS + [None, ""]:
        raise ValidationError("Pick a level")
    if "web_status" in data and data["web_status"] not in ("visible", "hidden"):
        raise ValidationError("Choose visible or hidden")
    if "design_key" in data:
        key = (data["design_key"] or "").strip()
        if key and (len(key) > 120 or not all(ch.isalnum() or ch in "-_" for ch in key)):
            raise ValidationError("Design key: letters, numbers, - and _ only")
        if key and await db.scalar(text("SELECT 1 FROM website_courses WHERE design_key=:k AND course_id<>:c"), {"k": key, "c": course_id}):
            raise ValidationError("Another course already uses this design key")
        data["design_key"] = key or None
    if "detail" in data:
        if data["detail"] in ("", None):
            data["detail"] = None
        elif not isinstance(data["detail"], dict) or len(json.dumps(data["detail"])) > 60_000:
            raise ValidationError("Programme page fields must be a JSON object (max 60 KB)")
    if "sort_order" in data:
        try:
            data["sort_order"] = int(data["sort_order"] or 100)
        except (TypeError, ValueError) as exc:
            raise ValidationError("Order must be a number") from exc
    for k in ("auto_instalments", "featured"):
        if k in data:
            data[k] = bool(data[k])
    if "programme_code" in data and data["programme_code"]:
        from app.modules.payments.db import transaction
        async with transaction() as c:
            if not await c.fetchval("SELECT 1 FROM programmes WHERE code=$1 AND active", data["programme_code"]):
                raise ValidationError("Pick an active Accounts programme")
    cols = list(data)
    params = {k: (json.dumps(v) if k == "detail" and v is not None else v) for k, v in data.items()}
    params.update(c=course_id, u=user.user_id)
    values = ", ".join((f"CAST(:{k} AS jsonb)" if k == "detail" else f":{k}") for k in cols)
    updates = ", ".join(f"{k}=EXCLUDED.{k}" for k in cols)
    await db.execute(text(f"""INSERT INTO website_courses(course_id{''.join(', ' + k for k in cols)}, updated_by) VALUES (:c{', ' + values if cols else ''}, :u)
        ON CONFLICT (course_id) DO UPDATE SET {updates + ', ' if updates else ''}updated_at=now(), updated_by=EXCLUDED.updated_by"""), params)
    await db.commit()
    service.clear_cache()
    return await get_listing(course_id, db)


@router.get("/readiness")
async def readiness(db: AsyncSession = Depends(get_db)):
    return {"data": await service.readiness(db)}


@router.get("/programmes")
async def accounts_programmes():
    """Accounts programmes a course can be billed under."""
    from app.modules.payments.db import transaction
    async with transaction() as c:
        return {"data": await rows(c, "SELECT code, name FROM programmes WHERE active ORDER BY name")}


# ---------------------------------------------------------------- FAQs and offers
TABLES = {"faqs": ("website_faqs", ("audience", "question", "answer", "sort_order", "status")),
          "campaigns": ("website_campaigns", ("key", "tag", "text", "short", "perk", "sort_order", "status"))}


def _clean(kind: str, body: dict) -> dict:
    table, fields = TABLES[kind]
    out = {k: body[k] for k in fields if k in body}
    for k, v in list(out.items()):
        if k == "sort_order":
            try:
                out[k] = int(v or 0)
            except (TypeError, ValueError) as exc:
                raise ValidationError("Order must be a number") from exc
        elif isinstance(v, str):
            out[k] = v.strip()[:4000]
        elif v is None:
            out[k] = ""
    if out.get("status", "draft") not in ("published", "draft"):
        raise ValidationError("Choose published or draft")
    if kind == "faqs":
        if out.get("audience", "student") not in ("student", "parent"):
            raise ValidationError("Shown to students or parents")
        if "question" in out and not out["question"]:
            raise ValidationError("Write the question")
    if kind == "campaigns" and "key" in out:
        if not out["key"] or not all(ch.isalnum() or ch in "-_" for ch in out["key"]) or len(out["key"]) > 40:
            raise ValidationError("Key: letters, numbers, - and _ only")
    return out


@router.get("/content/{kind}")
async def list_items(kind: str, db: AsyncSession = Depends(get_db)):
    if kind not in TABLES:
        raise NotFoundError("Unknown section")
    table = TABLES[kind][0]
    data = (await db.execute(text(f"SELECT * FROM {table} ORDER BY sort_order, id"))).mappings().all()
    return {"data": [service.jsonable(dict(r)) for r in data]}


@router.post("/content/{kind}")
async def create_item(kind: str, body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    if kind not in TABLES:
        raise NotFoundError("Unknown section")
    table = TABLES[kind][0]
    data = _clean(kind, body)
    if kind == "faqs" and not data.get("question"):
        raise ValidationError("Write the question")
    if kind == "campaigns" and not data.get("key"):
        raise ValidationError("Give the offer a key")
    cols = list(data)
    r = (await db.execute(text(f"INSERT INTO {table}({', '.join(cols)}) VALUES ({', '.join(':' + k for k in cols)}) RETURNING *"), data)).mappings().first()
    await db.commit()
    service.clear_cache()
    return {"data": service.jsonable(dict(r))}


@router.put("/content/{kind}/{item_id}")
async def update_item(kind: str, item_id: int, body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    if kind not in TABLES:
        raise NotFoundError("Unknown section")
    table = TABLES[kind][0]
    data = _clean(kind, body)
    if not data:
        raise ValidationError("Nothing to save")
    sets = ", ".join(f"{k}=:{k}" for k in data)
    r = (await db.execute(text(f"UPDATE {table} SET {sets}, updated_at=now() WHERE id=:id RETURNING *"), {**data, "id": item_id})).mappings().first()
    if not r:
        raise NotFoundError("Not found")
    await db.commit()
    service.clear_cache()
    return {"data": service.jsonable(dict(r))}


@router.delete("/content/{kind}/{item_id}")
async def delete_item(kind: str, item_id: int, db: AsyncSession = Depends(get_db)):
    if kind not in TABLES:
        raise NotFoundError("Unknown section")
    await db.execute(text(f"DELETE FROM {TABLES[kind][0]} WHERE id=:id"), {"id": item_id})
    await db.commit()
    service.clear_cache()
    return {"ok": True}


# ---------------------------------------------------------------- website settings
@router.get("/settings")
async def get_settings(db: AsyncSession = Depends(get_db)):
    return {"data": await service.settings(db)}


@router.put("/settings")
async def put_settings(body: dict = Body(...), user: CurrentUser = Depends(cms_admin), db: AsyncSession = Depends(get_db)):
    return {"data": await service.put_settings(db, body, user.user_id)}


# ---------------------------------------------------------------- demo classes (same editor shape as Course Studio)
@router.get("/demo-classes")
async def demo_classes(db: AsyncSession = Depends(get_db)):
    data = (await db.execute(text("SELECT * FROM web_demo_classes ORDER BY sort_order, id"))).mappings().all()
    return {"data": [demo.class_dict(dict(c)) for c in data]}


async def _class_row(db, class_id: int) -> dict:
    r = (await db.execute(text("SELECT * FROM web_demo_classes WHERE id=:id"), {"id": class_id})).mappings().first()
    if not r:
        raise NotFoundError("Class not found")
    return dict(r)


def _class_params(body: dict) -> dict:
    data = demo.clean_class(body)
    for k in ("lecturers", "takeaways"):
        if f"{k}_text" in body and k not in body:
            data[k] = str(body[f"{k}_text"] or "").strip()[:4000]
        elif isinstance(body.get(k), list):
            data[k] = (", " if k == "lecturers" else "\n").join(str(x).strip() for x in body[k] if str(x).strip())[:4000]
    return data


@router.post("/demo-classes")
async def create_class(body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    data = _class_params(body)
    if not data.get("title"):
        raise ValidationError("Give the class a title")
    cols = list(data)
    r = (await db.execute(text(f"INSERT INTO web_demo_classes({', '.join(cols)}) VALUES ({', '.join(':' + k for k in cols)}) RETURNING *"), data)).mappings().first()
    await db.commit()
    return {"data": demo.class_dict(dict(r))}


@router.put("/demo-classes/{class_id}")
async def update_class(class_id: int, body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    await _class_row(db, class_id)
    data = _class_params(body)
    if data:
        await db.execute(text(f"UPDATE web_demo_classes SET {', '.join(f'{k}=:{k}' for k in data)}, updated_at=now() WHERE id=:id"), {**data, "id": class_id})
        await db.commit()
    return {"data": demo.class_dict(await _class_row(db, class_id))}


@router.delete("/demo-classes/{class_id}")
async def delete_class(class_id: int, db: AsyncSession = Depends(get_db)):
    await db.execute(text("DELETE FROM web_demo_classes WHERE id=:id"), {"id": class_id})
    await db.commit()
    return {"ok": True}


@router.get("/demo-classes/{class_id}/studio")
async def studio(class_id: int, db: AsyncSession = Depends(get_db)):
    c = await _class_row(db, class_id)
    return {"data": {"class": demo.class_dict(c), "sections": await demo.class_tree(db, class_id)}}


@router.post("/demo-classes/{class_id}/sections")
async def add_section(class_id: int, body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    await _class_row(db, class_id)
    title = str(body.get("title") or "").strip()[:200]
    if not title:
        raise ValidationError("Give the section a title")
    pos = await db.scalar(text("SELECT COALESCE(MAX(position),0)+1 FROM web_demo_class_sections WHERE class_id=:c"), {"c": class_id})
    r = (await db.execute(text("""INSERT INTO web_demo_class_sections(class_id,title,description,status,position) VALUES (:c,:t,:d,:s,:p) RETURNING *"""),
                          {"c": class_id, "t": title, "d": str(body.get("description") or "")[:2000],
                           "s": "draft" if body.get("status") == "draft" else "active", "p": pos})).mappings().first()
    await db.commit()
    return {"data": {**service.jsonable(dict(r)), "items": []}}


@router.put("/demo-classes/{class_id}/sections/reorder")
async def reorder_sections(class_id: int, body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    for pos, sid in enumerate(body.get("ids") or [], start=1):
        await db.execute(text("UPDATE web_demo_class_sections SET position=:p WHERE id=:id AND class_id=:c"), {"p": pos, "id": int(sid), "c": class_id})
    await db.commit()
    return {"ok": True}


@router.put("/demo-sections/{section_id}")
async def edit_section(section_id: int, body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    title = str(body.get("title") or "").strip()[:200]
    if not title:
        raise ValidationError("Give the section a title")
    r = await db.execute(text("UPDATE web_demo_class_sections SET title=:t, description=:d, status=:s WHERE id=:id"),
                         {"t": title, "d": str(body.get("description") or "")[:2000], "s": "draft" if body.get("status") == "draft" else "active", "id": section_id})
    if not r.rowcount:
        raise NotFoundError("Section not found")
    await db.commit()
    return {"ok": True}


@router.delete("/demo-sections/{section_id}")
async def delete_section(section_id: int, db: AsyncSession = Depends(get_db)):
    await db.execute(text("DELETE FROM web_demo_class_sections WHERE id=:id"), {"id": section_id})
    await db.commit()
    return {"ok": True}


@router.post("/demo-sections/{section_id}/items")
async def add_item(section_id: int, body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    if not await db.scalar(text("SELECT 1 FROM web_demo_class_sections WHERE id=:id"), {"id": section_id}):
        raise NotFoundError("Section not found")
    data = demo.clean_item(body)
    data["position"] = await db.scalar(text("SELECT COALESCE(MAX(position),0)+1 FROM web_demo_class_items WHERE section_id=:s"), {"s": section_id})
    data["section_id"] = section_id
    cols = list(data)
    r = (await db.execute(text(f"INSERT INTO web_demo_class_items({', '.join(cols)}) VALUES ({', '.join(':' + k for k in cols)}) RETURNING *"), data)).mappings().first()
    await db.commit()
    return {"data": service.jsonable(dict(r))}


@router.put("/demo-sections/{section_id}/items/reorder")
async def reorder_items(section_id: int, body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    for pos, iid in enumerate(body.get("ids") or [], start=1):
        await db.execute(text("UPDATE web_demo_class_items SET position=:p WHERE id=:id AND section_id=:s"), {"p": pos, "id": int(iid), "s": section_id})
    await db.commit()
    return {"ok": True}


@router.put("/demo-items/{item_id}")
async def edit_item(item_id: int, body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    data = demo.clean_item(body)
    r = (await db.execute(text(f"UPDATE web_demo_class_items SET {', '.join(f'{k}=:{k}' for k in data)} WHERE id=:id RETURNING *"),
                          {**data, "id": item_id})).mappings().first()
    if not r:
        raise NotFoundError("Learning item not found")
    await db.commit()
    return {"data": service.jsonable(dict(r))}


@router.delete("/demo-items/{item_id}")
async def delete_item_route(item_id: int, db: AsyncSession = Depends(get_db)):
    await db.execute(text("DELETE FROM web_demo_class_items WHERE id=:id"), {"id": item_id})
    await db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- demo accounts
@router.get("/demo-accounts")
async def demo_accounts(db: AsyncSession = Depends(get_db)):
    data = (await db.execute(text("""SELECT id, email, full_name, phone, interest, created_at, expires_at, last_seen_at, crm_lead_id
        FROM web_demo_users ORDER BY created_at DESC LIMIT 500"""))).mappings().all()
    return {"data": [service.jsonable(dict(r)) for r in data]}


@router.post("/demo-accounts/{user_id}/extend")
async def extend_demo(user_id: str, db: AsyncSession = Depends(get_db)):
    r = await db.execute(text("UPDATE web_demo_users SET expires_at=GREATEST(expires_at, now()) + interval '24 hours' WHERE id=CAST(:u AS uuid)"), {"u": user_id})
    await db.execute(text("""UPDATE web_demo_sessions s SET expires_at=u.expires_at FROM web_demo_users u WHERE u.id=s.user_id AND u.id=CAST(:u AS uuid)"""), {"u": user_id})
    await db.commit()
    if not r.rowcount:
        raise NotFoundError("Demo account not found")
    return {"ok": True}


@router.delete("/demo-accounts/{user_id}")
async def delete_demo(user_id: str, db: AsyncSession = Depends(get_db)):
    await demo.purge_user(db, user_id)
    await db.commit()
    return {"ok": True}


# ================================================================ Payments console: website orders and slips
@pay_router.get("/orders")
async def web_orders(status: str | None = Query(None), c=Depends(payments_db, scope="function")):
    data = await rows(c, """SELECT o.id, o.reference, o.status, o.method, o.full_name, o.email, o.phone, o.course_title, o.plan_label, o.plan_code,
            o.amount, o.created_at, o.paid_at, o.possible_duplicate, o.is_test, o.crm_lead_id, o.crm_synced_at, o.lines,
            l.learner_no, l.id AS learner_id,
            (SELECT COUNT(*) FROM bank_slips s WHERE s.web_order_id=o.id AND s.status='pending') AS pending_slips
        FROM web_orders o LEFT JOIN learners l ON l.id=o.learner_id
        WHERE ($1::text IS NULL OR o.status=$1) ORDER BY o.created_at DESC LIMIT 500""", status)
    return data


@pay_router.post("/orders/{order_id}/cancel")
async def cancel_order(order_id: str, request: Request, user=Depends(finance), c=Depends(payments_db, scope="function")):
    return await once(c, request, user, {"order": order_id}, lambda: checkout.cancel_order(c, order_id, user.email))


@pay_router.post("/orders/{order_id}/record-reservation")
async def record_reservation(order_id: str, request: Request, user=Depends(finance), c=Depends(payments_db, scope="function")):
    """A reserved order with nothing paid: create the learner and enrolment so finance can set the plan."""
    async def run():
        o = await c.fetchrow("SELECT * FROM web_orders WHERE id=$1 FOR UPDATE", order_id)
        if not o:
            raise NotFoundError("Order not found")
        if o["status"] != "reserved" or o["learner_id"]:
            raise ValidationError("Only reserved orders without a learner can be recorded this way")
        r = await checkout.materialise(c, dict(o), method="bank_transfer", amount=0, received=await checkout.received_date(c, None), actor=user.email)
        return {k: r[k] for k in ("learner_no", "possible_duplicate")}
    return await once(c, request, user, {"order": order_id}, run)


@pay_router.get("/slips")
async def slips(status: str | None = Query("pending"), c=Depends(payments_db, scope="function")):
    return await rows(c, """SELECT s.id, s.status, s.amount, s.paid_on, s.reference, s.note, s.file_name, s.mime, s.size_bytes, s.reason,
            s.reviewed_by, s.reviewed_at, s.created_at,
            COALESCE(o.reference, v.number) AS ref, COALESCE(o.full_name, l.first_name||' '||l.last_name) AS student,
            COALESCE(o.course_title, v.description) AS what, COALESCE(o.amount, v.amount - v.amount_paid) AS expected
        FROM bank_slips s LEFT JOIN web_orders o ON o.id=s.web_order_id LEFT JOIN invoices v ON v.id=s.invoice_id
        LEFT JOIN learners l ON l.id=v.learner_id
        WHERE ($1::text IS NULL OR $1='all' OR s.status=$1) ORDER BY s.created_at DESC LIMIT 300""", status)


@pay_router.get("/slips/{slip_id}/file")
async def slip_file(slip_id: str, c=Depends(payments_db, scope="function")):
    s = await c.fetchrow("SELECT storage_key, mime, file_name FROM bank_slips WHERE id=$1", slip_id)
    if not s:
        raise NotFoundError("Slip not found")
    data = await checkout.read_slip_file(s["storage_key"])
    return {"mime": s["mime"], "file_name": s["file_name"], "data_base64": base64.b64encode(data).decode()}


@pay_router.post("/slips/{slip_id}/approve")
async def approve_slip(slip_id: str, request: Request, user=Depends(finance), c=Depends(payments_db, scope="function")):
    result = await once(c, request, user, {"slip": slip_id}, lambda: checkout.approve_slip(c, slip_id, user.email))
    return result


@pay_router.post("/slips/{slip_id}/reject")
async def reject_slip(slip_id: str, request: Request, b: dict = Body(...), user=Depends(finance), c=Depends(payments_db, scope="function")):
    return await once(c, request, user, {"slip": slip_id, "reason": b.get("reason")}, lambda: checkout.reject_slip(c, slip_id, b.get("reason", ""), user.email))


@pay_router.get("/enrolments")
async def web_enrolments(c=Depends(payments_db, scope="function")):
    return await rows(c, """SELECT w.*, e.learner_id, e.programme_code, e.total_fee, e.instalments, e.status, e.start_date, l.learner_no,
            l.first_name||' '||l.last_name AS learner_name, o.reference, o.plan_type,
            COALESCE((SELECT c.title FROM public.academic_courses c WHERE c.course_id=w.course_id), o.course_title) AS course_title,
            (SELECT COUNT(*) FROM instalments i WHERE i.enrolment_id=w.enrolment_id AND i.status='paid') AS instalments_paid,
            (SELECT COUNT(*) FROM instalments i WHERE i.enrolment_id=w.enrolment_id AND i.status<>'cancelled') AS instalments_total
        FROM web_enrolments w JOIN enrolments e ON e.id=w.enrolment_id JOIN learners l ON l.id=e.learner_id
        LEFT JOIN web_orders o ON o.id=w.web_order_id ORDER BY w.created_at DESC LIMIT 1000""")


@pay_router.post("/enrolments/{enrolment_id}/plan")
async def set_plan(enrolment_id: str, request: Request, b: dict = Body(...), user=Depends(finance), c=Depends(payments_db, scope="function")):
    return await once(c, request, user, b, lambda: checkout.set_plan(c, enrolment_id, plan_code=b.get("plan_code", "monthly"),
                                                                     instalments=int(b.get("instalments") or 1), amount=b.get("amount"),
                                                                     first_due=b.get("first_due"), actor=user.email))


@pay_router.post("/enrolments/{enrolment_id}/year2")
async def start_year2(enrolment_id: str, request: Request, b: dict = Body(...), user=Depends(finance), c=Depends(payments_db, scope="function")):
    return await once(c, request, user, b, lambda: checkout.start_year2(c, enrolment_id, instalments=int(b.get("instalments") or 0),
                                                                        amount=b.get("amount"), first_due=b.get("first_due"), actor=user.email))


@pay_router.post("/enrolments/{enrolment_id}/auto")
async def set_auto(enrolment_id: str, request: Request, b: dict = Body(...), user=Depends(finance), c=Depends(payments_db, scope="function")):
    return await once(c, request, user, b, lambda: checkout.set_auto(c, enrolment_id, bool(b.get("auto")), user.email))


@pay_router.post("/crm-sync")
async def run_crm_sync(user=Depends(finance)):
    return {"updated": await crm_sync.sync_paid_orders(50)}
