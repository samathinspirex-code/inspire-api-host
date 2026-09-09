"""Refresh pending Vimeo thumbnails without sending notifications or email."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import AsyncSessionLocal, engine  # noqa: E402
from sqlalchemy import select  # noqa: E402
from app.modules.lms.models import LmsLearningItem  # noqa: E402
from app.modules.lms import vimeo_service  # noqa: E402


async def main() -> None:
    try:
        async with AsyncSessionLocal() as db:
            refreshed = await vimeo_service.refresh_recent_learning_item_thumbnails(db, limit=100)
            recent_items = list(
                (
                    await db.execute(
                        select(LmsLearningItem)
                        .where(LmsLearningItem.item_type == "video")
                        .order_by(LmsLearningItem.updated_at.desc())
                        .limit(10)
                    )
                ).scalars()
            )
        print(f"Vimeo thumbnail refresh completed. Updated: {refreshed}")
        async with vimeo_service.VimeoClient() as vimeo:
            for item in recent_items:
                video_uri = vimeo_service.video_uri_from_url(item.resource_url)
                video = await vimeo.get_video(video_uri) if video_uri else {}
                current_vimeo_thumbnail = vimeo_service._thumbnail_url(video)
                vimeo_state = (
                    "missing" if not current_vimeo_thumbnail else
                    "generic" if not vimeo_service._is_ready_thumbnail(current_vimeo_thumbnail) else "ready"
                )
                transcode_status = ((video.get("transcode") or {}).get("status") or "unknown")
                picture_type = ((video.get("pictures") or {}).get("type") or "unknown")
                source = (item.thumbnail_url or "").lower()
                state = "missing" if not source else "generic" if "default" in source else "stored"
                print(f"Video {item.learning_item_id} ({item.title[:40]}): stored={state}; Vimeo={vimeo_state}; picture={picture_type}; transcode={transcode_status}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
