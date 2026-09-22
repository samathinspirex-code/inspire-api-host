"""Apply the configured LMS Vimeo embed policy to every linked video.

Run this after changing VIMEO_EMBED_DOMAINS in .env. The token stays server-side.
"""

import asyncio

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.errors import ValidationError
from app.modules.lms.models import LmsLearningItem
from app.modules.lms.vimeo_service import VimeoClient, video_uri_from_url


async def main() -> None:
    async with AsyncSessionLocal() as db:
        urls = (await db.execute(select(LmsLearningItem.resource_url).where(
            LmsLearningItem.item_type == "video",
        ))).scalars().all()
    video_uris = sorted({uri for url in urls if (uri := video_uri_from_url(url))})
    updated = 0
    failures: list[tuple[str, str]] = []
    async with VimeoClient() as vimeo:
        for video_uri in video_uris:
            try:
                await vimeo.update_video_privacy(video_uri)
                updated += 1
            except ValidationError as error:
                failures.append((video_uri, str(error)))
    print(f"Updated embed privacy for {updated} LMS Vimeo video(s).")
    if failures:
        print("Could not update:")
        for video_uri, reason in failures:
            print(f"- {video_uri}: {reason}")


if __name__ == "__main__":
    asyncio.run(main())
