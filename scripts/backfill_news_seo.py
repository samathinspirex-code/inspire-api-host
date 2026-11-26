"""One-time fill of AI meta title and meta description for published news posts.

Only Published posts with an empty meta title or meta description are touched. The URL slug is
never changed, so existing links and search results keep working.

    python scripts/backfill_news_seo.py          # dry run: prints what would be written
    python scripts/backfill_news_seo.py --apply  # writes the values
"""
import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import or_, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.database import AsyncSessionLocal  # noqa: E402
from app.modules.cms import news_seo  # noqa: E402
from app.modules.cms.models.news_event import NewsEvent  # noqa: E402


async def backfill(apply: bool, skip: set[str]) -> None:
    if not news_seo.openai_client.is_configured():
        print("OPENAI_API_KEY is not set; nothing to do.")
        return
    async with AsyncSessionLocal() as db:
        rows = list((await db.execute(
            select(NewsEvent)
            .where(NewsEvent.status == "Published",
                   or_(NewsEvent.meta_title.is_(None), NewsEvent.meta_title == "",
                       NewsEvent.meta_description.is_(None), NewsEvent.meta_description == ""))
            .order_by(NewsEvent.news_event_id)
        )).scalars().all())
        skipped = [row.slug for row in rows if row.slug in skip]
        rows = [row for row in rows if row.slug not in skip]
        for slug in skipped:
            print(f"Skipping /news/{slug} (--skip)")
        print(f"{len(rows)} published post(s) without meta. {'Writing' if apply else 'Dry run'}.\n")
        written = failed = 0
        for row in rows:
            seo = await news_seo.generate_news_seo(
                row.title, row.excerpt, list(row.content or []), kind=row.kind, category=row.category
            )
            if not seo:
                failed += 1
                print(f"#{row.news_event_id} /news/{row.slug}: AI failed, skipped\n")
                continue
            print(f"#{row.news_event_id} /news/{row.slug}\n"
                  f"  title ({len(seo['meta_title'])}): {seo['meta_title']}\n"
                  f"  description ({len(seo['meta_description'])}): {seo['meta_description']}\n")
            if apply:
                row.meta_title = seo["meta_title"]
                row.meta_description = seo["meta_description"]
                await db.commit()
                written += 1
        print(f"Done. {written} written, {failed} failed" + ("" if apply else " (dry run, nothing written)") + ".")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write the generated values")
    parser.add_argument("--skip", action="append", default=[], metavar="SLUG",
                        help="leave this post untouched (repeatable)")
    args = parser.parse_args()
    asyncio.run(backfill(args.apply, {slug.strip().strip("/").split("/")[-1] for slug in args.skip}))
