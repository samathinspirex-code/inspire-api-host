import asyncio
import json
from datetime import date
from types import SimpleNamespace

import pytest

from app.modules.cms import news_seo, service
from app.modules.cms.schemas.news_event import NewsEventCreate, NewsEventUpdate


def payload(**overrides):
    data = {
        "title": "Open day",
        "category": "Campus",
        "image_url": "https://cdn.example.com/cover.jpg",
        "excerpt": "Short summary",
        "content": ["First paragraph"],
        "author": "Inspire",
    }
    data.update(overrides)
    return data


class FakeRepo:
    def __init__(self, rows=()):
        self.rows = {row.news_event_id: row for row in rows}
        self.created = None
        self.updated = None

    async def get_by_slug(self, slug):
        return next((row for row in self.rows.values() if row.slug == slug), None)

    async def get(self, news_event_id):
        return self.rows.get(news_event_id)

    async def create(self, data):
        self.created = data
        return data

    async def update(self, item, data):
        self.updated = data
        return data


@pytest.fixture
def repo(monkeypatch):
    holder = {"repo": FakeRepo()}
    monkeypatch.setattr(service, "NewsEventRepository", lambda db: holder["repo"])

    async def no_media(db, value):
        return value

    monkeypatch.setattr(service.media_service, "resolve_media_reference", no_media)
    monkeypatch.setattr(service.NewsEventItem, "model_validate", staticmethod(lambda value: value))
    return holder


def fake_ai(monkeypatch, result):
    calls = []

    async def generate(*args, **kwargs):
        calls.append(args)
        return result

    monkeypatch.setattr(service.news_seo, "generate_news_seo", generate)
    return calls


AI = {"slug": "inspire-open-day-2026", "meta_title": "Open Day at Our Colombo Campus",
      "meta_description": "Visit the campus, meet lecturers and explore programmes at the open day."}


def test_create_uses_ai_slug_and_meta(repo, monkeypatch):
    fake_ai(monkeypatch, AI)
    data = asyncio.run(service.create_news_event(None, NewsEventCreate(**payload(slug="ignored-by-server"))))
    assert data["slug"] == "inspire-open-day-2026"
    assert data["meta_title"] == AI["meta_title"]
    assert data["meta_description"] == AI["meta_description"]


def test_duplicate_slug_gets_a_number(repo, monkeypatch):
    repo["repo"] = FakeRepo([SimpleNamespace(news_event_id=1, slug="inspire-open-day-2026"),
                             SimpleNamespace(news_event_id=2, slug="inspire-open-day-2026-2")])
    fake_ai(monkeypatch, AI)
    data = asyncio.run(service.create_news_event(None, NewsEventCreate(**payload())))
    assert data["slug"] == "inspire-open-day-2026-3"


def test_ai_failure_still_saves(repo, monkeypatch):
    fake_ai(monkeypatch, None)
    data = asyncio.run(service.create_news_event(None, NewsEventCreate(**payload(title="Open Day 2026!"))))
    assert data["slug"] == "open-day-2026"
    assert data["meta_title"] is None and data["meta_description"] is None


def test_non_latin_title_falls_back_to_dated_slug():
    assert news_seo.fallback_slug("විවෘත දිනය", date(2026, 11, 26)) == "news-2026-11-26"


def existing(**overrides):
    row = dict(news_event_id=7, slug="old-link", title="Open day", excerpt="Short summary",
               content=["First paragraph"], meta_title="Old title", meta_description="Old description")
    row.update(overrides)
    return SimpleNamespace(**row)


def test_edit_keeps_slug_and_skips_ai_when_text_unchanged(repo, monkeypatch):
    repo["repo"] = FakeRepo([existing()])
    calls = fake_ai(monkeypatch, AI)
    data = asyncio.run(service.update_news_event(None, 7, NewsEventUpdate(**payload(slug="new-link"))))
    assert data["slug"] == "old-link"
    assert calls == []
    assert "meta_title" not in data


def test_edit_regenerates_meta_but_not_slug_when_text_changes(repo, monkeypatch):
    repo["repo"] = FakeRepo([existing()])
    calls = fake_ai(monkeypatch, AI)
    data = asyncio.run(service.update_news_event(None, 7, NewsEventUpdate(**payload(title="Open day moved"))))
    assert len(calls) == 1
    assert data["slug"] == "old-link"
    assert data["meta_title"] == AI["meta_title"]


def test_edit_clears_stale_meta_when_ai_fails(repo, monkeypatch):
    repo["repo"] = FakeRepo([existing()])
    fake_ai(monkeypatch, None)
    data = asyncio.run(service.update_news_event(None, 7, NewsEventUpdate(**payload(excerpt="New summary"))))
    assert data["meta_title"] is None and data["meta_description"] is None


def test_edit_fills_missing_meta(repo, monkeypatch):
    repo["repo"] = FakeRepo([existing(meta_title=None, meta_description=None)])
    calls = fake_ai(monkeypatch, AI)
    asyncio.run(service.update_news_event(None, 7, NewsEventUpdate(**payload())))
    assert len(calls) == 1


def test_meta_fields_cannot_be_sent_by_the_cms():
    assert "meta_title" not in NewsEventCreate.model_fields
    assert "meta_description" not in NewsEventCreate.model_fields


def test_generator_cleans_and_limits_the_reply(monkeypatch):
    reply = {
        "slug": "  Open Day: Colombo Campus — Visit Us Today and Meet Lecturers About Business and Computing Degrees ",
        "meta_title": "\"Open Day at the Colombo Campus\"",
        "meta_description": "word " * 60,
    }

    async def respond(*args, **kwargs):
        assert kwargs["json_schema"]["name"] == "news_seo"
        return json.dumps(reply)

    monkeypatch.setattr(news_seo.openai_client, "is_configured", lambda: True)
    monkeypatch.setattr(news_seo.openai_client, "respond", respond)
    result = asyncio.run(news_seo.generate_news_seo("Open day", "Summary", ["Body"]))
    assert result["meta_title"] == "Open Day at the Colombo Campus"
    assert len(result["slug"]) <= news_seo.SLUG_MAX and not result["slug"].endswith("-")
    assert result["slug"].startswith("open-day-colombo-campus")
    assert len(result["meta_description"]) <= news_seo.META_DESCRIPTION_MAX
    assert not result["meta_description"].endswith(" ")


def test_long_description_ends_on_a_full_sentence():
    first = "Inspire College and Allianz sign an MoU to support practical professional development for Allianz staff."
    text = first + " Staff build workplace skills through flexible learning alongside their everyday work and family duties."
    assert news_seo._clean_description(text) == first


def test_long_single_sentence_drops_dangling_words():
    text = "Helping managers apply AI to communication, analytics and " + "planning " * 20
    result = news_seo._clean_description(text)
    assert len(result) <= news_seo.META_DESCRIPTION_MAX and result.endswith(".")
    assert not result.endswith(" and.")


def test_long_description_cuts_at_a_comma_not_inside_a_name():
    text = ("Inspire College partners with LSBF Singapore to offer live online Business and Computing programs, "
            "global qualifications, and career-focused learning in Sri Lanka for working adults.")
    assert news_seo._clean_description(text) == (
        "Inspire College partners with LSBF Singapore to offer live online Business and Computing programs, "
        "global qualifications."
    )


def test_missing_full_stop_is_added():
    assert news_seo._clean_description("Stay connected while learning") == "Stay connected while learning."


def test_too_long_reply_is_retried_once(monkeypatch):
    replies = [
        {"slug": "a-b", "meta_title": "Title", "meta_description": "x " * 100},
        {"slug": "a-b", "meta_title": "Title", "meta_description": "A short complete sentence."},
    ]
    seen = []

    async def respond(instructions, conversation, **kwargs):
        seen.append(conversation)
        return json.dumps(replies[len(seen) - 1])

    monkeypatch.setattr(news_seo.openai_client, "is_configured", lambda: True)
    monkeypatch.setattr(news_seo.openai_client, "respond", respond)
    result = asyncio.run(news_seo.generate_news_seo("T", "S", ["B"]))
    assert len(seen) == 2 and "Too long" in seen[1][-1]["content"]
    assert result["meta_description"] == "A short complete sentence."


def test_generator_returns_none_when_not_configured(monkeypatch):
    monkeypatch.setattr(news_seo.openai_client, "is_configured", lambda: False)
    assert asyncio.run(news_seo.generate_news_seo("Open day", "Summary", ["Body"])) is None


def test_generator_returns_none_for_bad_json(monkeypatch):
    async def respond(*args, **kwargs):
        return "not json"

    monkeypatch.setattr(news_seo.openai_client, "is_configured", lambda: True)
    monkeypatch.setattr(news_seo.openai_client, "respond", respond)
    assert asyncio.run(news_seo.generate_news_seo("Open day", "Summary", ["Body"])) is None
