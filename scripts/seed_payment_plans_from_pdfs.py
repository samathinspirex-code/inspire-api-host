"""Seed course payment plans from the HND and Level 3 Foundation payment plan PDFs.

Dry run by default: prints the courses each plan would be written to.
Pass --apply to write the plans (and switch them on) in academic_courses and programs.
Pass --only-course-ids 1,2,3 to limit the write to specific courses.
"""
import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import settings  # noqa: E402

DUE = "Within 1 month of the course start date"

HND_PLAN = {
    "heading": "HND – Payment Plan",
    "highlight": "Start your HND with LKR 9,900",
    "intro": "",
    "cards": [
        {
            "eyebrow": "YEAR 1", "title": "Year 1", "amount": "LKR 99,100", "amount_note": "full year fee",
            "badge": "", "featured": True, "footnote": "",
            "rows": [
                {"label": "Year 1 fee", "calculation": "Full year price", "amount": "LKR 99,100", "emphasis": False},
                {"label": "Registration fee", "calculation": "Paid once", "amount": "LKR 10,000", "emphasis": False},
                {"label": "Balance", "calculation": "LKR 99,100 − LKR 10,000", "amount": "LKR 89,100", "emphasis": False},
                {"label": "Monthly instalment", "calculation": "9 monthly payments", "amount": "LKR 9,900 per month", "emphasis": True},
            ],
        },
        {
            "eyebrow": "YEAR 2", "title": "Year 2", "amount": "LKR 150,000", "amount_note": "full year fee",
            "badge": "", "featured": False, "footnote": "",
            "rows": [
                {"label": "Year 2 fee", "calculation": "Full year price", "amount": "LKR 150,000", "emphasis": False},
                {"label": "Registration fee", "calculation": "Paid once", "amount": "LKR 10,000", "emphasis": False},
                {"label": "Balance", "calculation": "LKR 150,000 − LKR 10,000", "amount": "LKR 140,000", "emphasis": False},
                {"label": "Monthly instalment", "calculation": "LKR 140,000 ÷ 9 months", "amount": "LKR 15,560 per month", "emphasis": True},
            ],
        },
    ],
    "extra_fees": {
        "title": "Additional fee (GBP)",
        "note": "The GBP fees are separate from the LKR fees above.",
        "rows": [
            {"label": "Year 1", "amount": "GBP 179", "due": DUE},
            {"label": "Year 2", "amount": "GBP 193", "due": DUE},
        ],
    },
}

FOUNDATION_PLAN = {
    "heading": "Level 3 Foundation – Payment Plan",
    "highlight": "",
    "intro": "",
    "cards": [
        {
            "eyebrow": "PROGRAMME FEE", "title": "Level 3 Foundation", "amount": "LKR 49,500", "amount_note": "total programme fee",
            "badge": "", "featured": True, "footnote": "",
            "rows": [
                {"label": "Registration fee", "calculation": "Paid once (non-refundable)", "amount": "LKR 9,500", "emphasis": False},
                {"label": "Monthly instalments", "calculation": "LKR 5,000 × 8 months", "amount": "LKR 40,000", "emphasis": False},
                {"label": "Total programme fee", "calculation": "LKR 9,500 + LKR 40,000", "amount": "LKR 49,500", "emphasis": True},
            ],
        },
    ],
    "extra_fees": {
        "title": "Additional fee (GBP)",
        "note": "This is separate from the LKR programme fee above.",
        "rows": [{"label": "Course", "amount": "GBP 90", "due": DUE}],
    },
}


# Additional fees now live inside each card (Year 1 fee in the Year 1 card, and so on).
from migrate_limited_offer_and_card_fees import fees_into_cards  # noqa: E402

HND_PLAN = fees_into_cards(HND_PLAN)
FOUNDATION_PLAN = fees_into_cards(FOUNDATION_PLAN)


def plan_for(title: str, programme: str):
    text = f"{title} {programme}".casefold()
    if re.search(r"\bhnd\b", text):
        return "HND", HND_PLAN
    if "level 3" in text or "foundation" in text:
        return "Level 3 Foundation", FOUNDATION_PLAN
    return None, None


async def main(apply: bool, only_ids: set[int] | None) -> None:
    connection = await asyncpg.connect(
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        database=settings.POSTGRES_DB,
        ssl="require" if settings.POSTGRES_HOST not in {"localhost", "127.0.0.1"} else None,
    )
    try:
        rows = await connection.fetch("""
            SELECT c.course_id, c.legacy_program_id, c.title, c.slug, c.status, p.name AS programme
            FROM academic_courses c JOIN academic_programmes p ON p.programme_id = c.programme_id
            ORDER BY p.name, c.title
        """)
        matches = []
        for row in rows:
            name, plan = plan_for(row["title"], row["programme"])
            if plan and (only_ids is None or row["course_id"] in only_ids):
                matches.append((row, name, plan))
        for row, name, _ in matches:
            print(f"[{name}] course {row['course_id']} · {row['title']} · {row['programme']} · {row['slug']} · {row['status']}")
        print(f"{len(matches)} course(s) matched.")
        if not apply:
            print("Dry run only. Re-run with --apply to write these plans.")
            return
        async with connection.transaction():
            for row, _, plan in matches:
                payload = json.dumps(plan, ensure_ascii=False)
                await connection.execute(
                    "UPDATE academic_courses SET payment_plans_enabled = TRUE, payment_plans = $1::jsonb, updated_at = now() WHERE course_id = $2",
                    payload, row["course_id"],
                )
                if row["legacy_program_id"]:
                    await connection.execute(
                        "UPDATE programs SET payment_plans_enabled = TRUE, payment_plans = $1::jsonb WHERE program_id = $2",
                        payload, row["legacy_program_id"],
                    )
        print(f"Applied payment plans to {len(matches)} course(s).")
    finally:
        await connection.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--only-course-ids", default="")
    args = parser.parse_args()
    ids = {int(x) for x in args.only_course_ids.split(",") if x.strip()} or None
    asyncio.run(main(args.apply, ids))
