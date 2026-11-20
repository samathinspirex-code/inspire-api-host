"""Move the limited offer to course level and fold plan-level additional fees into each card.

Dry run by default: prints what would change for every course with a payment plan.
Pass --apply to write academic_courses and the programs mirror in one transaction.

1. A course whose plan has any card with limited_offer=true gets limited_offer_enabled=true
   (keeping that card's custom label).
2. Plan-level extra_fees rows move into the card whose title or eyebrow matches the row label
   ("Year 1" -> "Year 1" card). A single-card plan takes every row; unmatched rows go to the
   first card. Each receiving card gets the plan's fee title and note.

The old fields (plan-level extra_fees, card-level offer flags) are left in place so the
currently deployed website keeps working; the new code ignores them and the CMS drops
them on the next save. Safe to re-run: cards that already have fee rows are not refilled.
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import settings  # noqa: E402

DEFAULT_LABEL = "Limited Offer"


def _norm(value) -> str:
    return " ".join(str(value or "").casefold().split())


def fees_into_cards(plan: dict, keep_legacy: bool = False) -> dict:
    """Return a copy of plan with plan-level fee rows moved into matching cards."""
    plan = json.loads(json.dumps(plan))
    cards = plan.get("cards") or []
    legacy = plan.get("extra_fees") or {}
    rows = [r for r in (legacy.get("rows") or []) if any(str(r.get(k) or "").strip() for k in ("label", "amount", "due"))]
    for card in cards:
        card.setdefault("extra_fees", {"title": "", "note": "", "rows": []})
        card["extra_fees"].setdefault("rows", [])
    already_split = any(card["extra_fees"]["rows"] for card in cards)
    if not cards or not rows or already_split:
        if not keep_legacy:
            plan["extra_fees"] = {"title": "", "note": "", "rows": []}
        return plan
    touched = set()
    for row in rows:
        target = 0
        if len(cards) > 1:
            label = _norm(row.get("label"))
            for i, card in enumerate(cards):
                if label and label in (_norm(card.get("title")), _norm(card.get("eyebrow"))):
                    target = i
                    break
        cards[target]["extra_fees"]["rows"].append(row)
        touched.add(target)
    for i in touched:
        fees = cards[i]["extra_fees"]
        fees["title"] = fees.get("title") or legacy.get("title") or ""
        fees["note"] = fees.get("note") or legacy.get("note") or ""
    if not keep_legacy:
        plan["extra_fees"] = {"title": "", "note": "", "rows": []}
    return plan


def card_offer(plan: dict):
    """Return (any card has the offer on, first custom label) without changing the plan."""
    any_on, label = False, ""
    for card in plan.get("cards") or []:
        if card.get("limited_offer"):
            any_on = True
            label = label or str(card.get("limited_offer_label") or "").strip()
    return any_on, label


async def main(apply: bool) -> None:
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
            SELECT course_id, legacy_program_id, title, payment_plans, limited_offer_enabled, limited_offer_label
            FROM academic_courses
            WHERE jsonb_array_length(COALESCE(payment_plans->'cards', '[]'::jsonb)) > 0
            ORDER BY title
        """)
        updates = []
        for row in rows:
            raw = row["payment_plans"]
            plan = json.loads(raw) if isinstance(raw, str) else dict(raw)
            new_plan = fees_into_cards(plan, keep_legacy=True)
            offer_on, label = card_offer(new_plan)
            enabled = bool(row["limited_offer_enabled"]) or offer_on
            new_label = label or row["limited_offer_label"] or DEFAULT_LABEL
            fee_map = "; ".join(
                f"{card.get('title') or card.get('eyebrow') or f'card {i + 1}'}: "
                + (", ".join(f"{r.get('label')} {r.get('amount')}" for r in card["extra_fees"]["rows"]) or "-")
                for i, card in enumerate(new_plan["cards"])
            )
            print(f"course {row['course_id']} · {row['title']} · offer {'ON' if enabled else 'off'} ('{new_label}') · fees -> {fee_map}")
            updates.append((row, new_plan, enabled, new_label))
        print(f"{len(updates)} course(s) with payment plans.")
        if not apply:
            print("Dry run only. Re-run with --apply to write these changes.")
            return
        async with connection.transaction():
            for row, plan, enabled, label in updates:
                payload = json.dumps(plan, ensure_ascii=False)
                await connection.execute(
                    "UPDATE academic_courses SET payment_plans=$1::jsonb, limited_offer_enabled=$2, limited_offer_label=$3, updated_at=now() WHERE course_id=$4",
                    payload, enabled, label, row["course_id"],
                )
                if row["legacy_program_id"]:
                    await connection.execute(
                        "UPDATE programs SET payment_plans=$1::jsonb, limited_offer_enabled=$2, limited_offer_label=$3 WHERE program_id=$4",
                        payload, enabled, label, row["legacy_program_id"],
                    )
        print(f"Applied to {len(updates)} course(s).")
    finally:
        await connection.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    asyncio.run(main(parser.parse_args().apply))
