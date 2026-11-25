from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.modules.cms.schemas.news_event import NewsEventCreate, NewsEventItem


def payload(**overrides):
    data = {
        "slug": "open-day",
        "title": "Open day",
        "category": "Campus",
        "image_url": "https://cdn.example.com/cover.jpg",
        "excerpt": "Short summary",
        "content": ["First paragraph"],
        "author": "Inspire",
    }
    data.update(overrides)
    return data


def test_post_without_video_has_no_video_fields():
    item = NewsEventCreate(**payload(video_orientation="portrait", video_poster_url="https://cdn.example.com/p.jpg"))
    assert item.video_url is None
    assert item.video_orientation is None
    assert item.video_poster_url is None


def test_video_defaults_to_landscape():
    item = NewsEventCreate(**payload(video_url=" https://cdn.example.com/clip.mp4 "))
    assert item.video_url == "https://cdn.example.com/clip.mp4"
    assert item.video_orientation == "landscape"


def test_portrait_video_is_kept():
    item = NewsEventCreate(**payload(video_url="https://cdn.example.com/clip.mp4", video_orientation="portrait"))
    assert item.video_orientation == "portrait"


def test_media_library_name_is_allowed():
    item = NewsEventCreate(**payload(video_url="open-day-clip-12"))
    assert item.video_url == "open-day-clip-12"


@pytest.mark.parametrize("url", ["http://cdn.example.com/clip.mp4", "javascript://x", "https://user:pw@cdn.example.com/a.mp4"])
def test_non_https_video_is_rejected(url):
    with pytest.raises(ValidationError):
        NewsEventCreate(**payload(video_url=url))


def test_bad_orientation_is_rejected():
    with pytest.raises(ValidationError):
        NewsEventCreate(**payload(video_url="https://cdn.example.com/clip.mp4", video_orientation="square"))


def test_item_returns_video_fields():
    now = datetime.now(timezone.utc)
    row = SimpleNamespace(
        news_event_id=1, slug="open-day", title="Open day", kind="News", category="Campus",
        image_url="https://cdn.example.com/cover.jpg", excerpt="x", content=["y"], author="Inspire",
        status="Published", published_on=None, event_date=None,
        video_url="https://cdn.example.com/clip.mp4", video_orientation="portrait",
        video_poster_url=None, created_at=now, updated_at=now,
    )
    item = NewsEventItem.model_validate(row).model_dump()
    assert item["video_url"] == "https://cdn.example.com/clip.mp4"
    assert item["video_orientation"] == "portrait"
