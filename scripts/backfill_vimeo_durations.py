"""Refresh legacy one-minute Vimeo placeholders from Vimeo's video metadata."""

import argparse
import asyncio

from sqlalchemy import text

from app.core.config import settings

settings.ENVIRONMENT = "production"  # Keep SQL diagnostics and credentials out of output.
from app.core.database import AsyncSessionLocal, engine  # noqa: E402
from app.modules.lms.vimeo_service import VimeoClient, video_uri_from_url  # noqa: E402


async def main(apply: bool) -> None:
    async with AsyncSessionLocal() as db:
        urls = list((await db.execute(text("""
            SELECT DISTINCT resource_url FROM lms_learning_items
            WHERE item_type = 'video' AND resource_url LIKE '%vimeo%'
              AND (duration_minutes IS NULL OR duration_minutes = 1)
        """))).scalars().all())
    print(f"Checking {len(urls)} Vimeo videos with missing or one-minute durations.", flush=True)

    semaphore = asyncio.Semaphore(4)
    results: list[tuple[str, int | None, str]] = []

    async with VimeoClient() as vimeo:
        async def fetch(url: str) -> tuple[str, int | None, str]:
            uri = video_uri_from_url(url)
            if not uri:
                return url, None, "invalid_url"
            async with semaphore:
                for attempt in range(3):
                    try:
                        video = await vimeo.get_video(uri)
                        seconds = int(video.get("duration") or 0)
                        return url, max(1, round(seconds / 60)) if seconds > 0 else None, "ready" if seconds > 0 else "processing"
                    except Exception as exc:
                        if "busy" not in str(exc).lower() or attempt == 2:
                            return url, None, "rate_limited" if "busy" in str(exc).lower() else "unavailable"
                        await asyncio.sleep(2 ** attempt)
            return url, None, "unavailable"

        for index in range(0, len(urls), 40):
            results.extend(await asyncio.gather(*(fetch(url) for url in urls[index:index + 40])))
            print(f"Checked {min(index + 40, len(urls))}/{len(urls)}.", flush=True)

    corrections = [(url, minutes) for url, minutes, _ in results if minutes and minutes > 1]
    processing_urls = [url for url, _, reason in results if reason == "processing"]
    verified_short = sum(minutes == 1 for _, minutes, _ in results)
    unresolved = {reason: sum(result_reason == reason for _, _, result_reason in results) for reason in ("processing", "rate_limited", "unavailable", "invalid_url")}
    print(f"Found {len(corrections)} longer videos; {verified_short} are one minute; unresolved: {unresolved}.")
    if apply and (corrections or processing_urls):
        async with AsyncSessionLocal() as db:
            for url, minutes in corrections:
                await db.execute(text("""
                    UPDATE lms_learning_items SET duration_minutes = :minutes
                    WHERE item_type = 'video' AND resource_url = :url
                      AND (duration_minutes IS NULL OR duration_minutes = 1)
                """), {"minutes": minutes, "url": url})
            for url in processing_urls:
                await db.execute(text("""
                    UPDATE lms_learning_items SET duration_minutes = NULL
                    WHERE item_type = 'video' AND resource_url = :url
                      AND duration_minutes = 1
                """), {"url": url})
            await db.commit()
        print(f"Saved {len(corrections)} verified durations and cleared {len(processing_urls)} unverified one-minute placeholders.")
    elif corrections or processing_urls:
        print("Dry run only. Pass --apply to save verified durations.")
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    asyncio.run(main(parser.parse_args().apply))
