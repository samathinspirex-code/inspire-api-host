"""Copy the new website's own content into this database (once). Existing rows are left alone unless --update.

Seed file: app/modules/website/seed/website_seed.json (exported from the new website's old database: course listings
matched by catalogue slug, FAQs, offers, website settings, story photos, the demo class and its content).

    python scripts/seed_website_content.py --dry-run     # show what would change
    python scripts/seed_website_content.py               # insert what is missing
    python scripts/seed_website_content.py --update      # also overwrite course listings from the seed
"""
import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import text  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import AsyncSessionLocal  # noqa: E402
from app.modules.payments.db import connect  # noqa: E402

SEED = Path(__file__).resolve().parents[1] / "app/modules/website/seed/website_seed.json"
LISTING = ("design_key", "plan_type", "short_name", "level", "school", "body_label", "card_type", "keywords", "monthly_label", "fee_label",
           "cpd_addon_fee", "auto_instalments", "featured", "sort_order", "web_status", "detail", "seo_title", "seo_description")


async def copy_media(url: str, dry: bool) -> str:
    """Files uploaded to the old website backend (Render) move to the media bucket."""
    if not url or "onrender.com/media/" not in url or dry:
        return url
    try:
        import httpx
        from app.modules.cms import media_service
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.get(url)
            r.raise_for_status()
        name = url.rsplit("/", 1)[-1][:120]
        key = f"website/demo/{name}"
        await asyncio.to_thread(media_service._client().put_object, Bucket=settings.MEDIA_BUCKET, Key=key, Body=r.content,
                                ContentType=r.headers.get("content-type", "application/octet-stream"))
        return settings.MEDIA_PUBLIC_BASE_URL.rstrip("/") + "/" + key
    except Exception as exc:  # keep the old link; fix it later in the CMS
        print(f"  ! could not copy {url}: {type(exc).__name__}")
        return url


async def main(dry: bool, update: bool) -> None:
    seed = json.loads(SEED.read_text(encoding="utf-8"))
    async with AsyncSessionLocal() as db:
        slugs = {r["slug"]: r["course_id"] for r in (await db.execute(text("SELECT slug, course_id FROM academic_courses"))).mappings()}
        existing = {r[0] for r in (await db.execute(text("SELECT course_id FROM website_courses"))).all()}
        added = updated = 0
        missing = []
        for row in seed["courses"]:
            cid = slugs.get(row["slug"])
            if not cid:
                missing.append(row["slug"])
                continue
            if cid in existing and not update:
                continue
            values = {k: row.get(k) for k in LISTING}
            values["detail"] = json.dumps(values["detail"]) if values["detail"] is not None else None
            if not dry:
                cols = ", ".join(LISTING)
                vals = ", ".join(("CAST(:detail AS jsonb)" if k == "detail" else f":{k}") for k in LISTING)
                sets = ", ".join(f"{k}=EXCLUDED.{k}" for k in LISTING)
                await db.execute(text(f"""INSERT INTO website_courses(course_id, {cols}) VALUES (:cid, {vals})
                    ON CONFLICT (course_id) DO UPDATE SET {sets}, updated_at=now()"""), {**values, "cid": cid})
            if cid in existing:
                updated += 1
            else:
                added += 1
        print(f"Course listings: {added} new, {updated} updated, {len(missing)} not in the catalogue")
        for s in missing:
            print("  - not found:", s)

        for table, key, rows in (("website_faqs", "question", seed["faqs"]), ("website_campaigns", "key", seed["campaigns"])):
            have = {r[0] for r in (await db.execute(text(f"SELECT {key} FROM {table}"))).all()}
            todo = [r for r in rows if r[key] not in have]
            print(f"{table}: {len(todo)} to add")
            for r in todo if not dry else []:
                cols = list(r)
                await db.execute(text(f"INSERT INTO {table}({', '.join(cols)}) VALUES ({', '.join(':' + c for c in cols)})"), r)

        settings_rows = dict(seed["settings"])
        settings_rows["stories"] = [{k: s.get(k) for k in ("name", "role", "image", "image_pos", "video_url")} for s in seed["stories"]]
        if not settings_rows.get("fee_plans"):
            settings_rows.pop("fee_plans", None)
        have = {r[0] for r in (await db.execute(text("SELECT key FROM website_settings"))).all()}
        todo = {k: v for k, v in settings_rows.items() if k not in have}
        print("website_settings to add:", ", ".join(todo) or "none")
        for k, v in todo.items() if not dry else []:
            await db.execute(text("INSERT INTO website_settings(key, value) VALUES (:k, CAST(:v AS jsonb))"), {"k": k, "v": json.dumps(v)})

        have_classes = {r[0] for r in (await db.execute(text("SELECT title FROM web_demo_classes"))).all()}
        for cls in seed.get("demo_classes", []):
            if cls["title"] in have_classes:
                continue
            print(f"Demo class to add: {cls['title']} ({sum(len(s['items']) for s in cls['sections'])} items)")
            if dry:
                continue
            fields = {k: cls[k] for k in ("code", "title", "awarding_body", "school", "programme", "intake", "study_mode", "lecturers", "status",
                                          "description", "takeaways", "sort_order", "published")}
            fields["image"] = await copy_media(cls.get("image") or "", dry)
            fields["start_date"] = date.fromisoformat(cls["start_date"]) if cls.get("start_date") else None
            fields["end_date"] = date.fromisoformat(cls["end_date"]) if cls.get("end_date") else None
            class_id = await db.scalar(text("""INSERT INTO web_demo_classes(code,title,awarding_body,school,programme,intake,study_mode,lecturers,status,
                    description,takeaways,sort_order,published,image,start_date,end_date)
                VALUES (:code,:title,:awarding_body,:school,:programme,:intake,:study_mode,:lecturers,:status,:description,:takeaways,:sort_order,
                    :published,:image,:start_date,:end_date) RETURNING id"""), fields)
            for sec in cls["sections"]:
                sid = await db.scalar(text("""INSERT INTO web_demo_class_sections(class_id,title,description,status,position)
                    VALUES (:c,:t,:d,:s,:p) RETURNING id"""), {"c": class_id, "t": sec["title"], "d": sec["description"] or "", "s": sec["status"], "p": sec["position"]})
                for it in sec["items"]:
                    await db.execute(text("""INSERT INTO web_demo_class_items(section_id,item_type,title,description,resource_url,text_content,
                            duration_minutes,status,is_required,position) VALUES (:s,:item_type,:title,:description,:resource_url,:text_content,
                            :duration_minutes,:status,:is_required,:position)"""),
                                     {**it, "s": sid, "description": it["description"] or "", "text_content": it["text_content"] or "",
                                      "resource_url": await copy_media(it["resource_url"] or "", dry)})
        if not dry:
            await db.commit()

    billing = seed.get("billing") or {}
    c = await connect()
    try:
        if not await c.fetchval("SELECT 1 FROM settings WHERE key='web_checkout'"):
            from app.modules.payments.core import DEFAULTS
            value = {**DEFAULTS["web_checkout"], "bank": {**DEFAULTS["web_checkout"]["bank"], **(billing.get("bank") or {})},
                     "finance_email": billing.get("finance_email") or ""}
            print("Accounts setting web_checkout: will add (bank details stay blank until finance fills them in)")
            if not dry:
                await c.execute("INSERT INTO settings(key, value) VALUES ('web_checkout', $1::jsonb) ON CONFLICT DO NOTHING", json.dumps(value))
    finally:
        await c.close()
    print("Dry run only. Nothing was written." if dry else "Seed done.")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--update", action="store_true", help="overwrite course listings from the seed file")
    a = p.parse_args()
    asyncio.run(main(a.dry_run, a.update))
