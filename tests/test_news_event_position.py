import asyncio

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.dialects import postgresql

from app.core.errors import ValidationError
from app.modules.cms import service
from app.modules.cms.repository.news_event import WEBSITE_ORDER, NewsEventRepository
from app.modules.cms.router import router
from app.modules.cms.schemas import NewsEventReorderRequest
from app.modules.cms.schemas.news_event import NewsEventCreate


def test_reorder_route_is_matched_before_the_id_route():
    paths = [(route.path, sorted(route.methods)) for route in router.routes if "news-events" in route.path]
    reorder = paths.index(("/api/v1/cms/news-events/reorder", ["PUT"]))
    by_id = paths.index(("/api/v1/cms/news-events/{news_event_id}", ["PUT"]))
    assert reorder < by_id


def test_reorder_rejects_duplicates_without_touching_the_db():
    with pytest.raises(ValidationError):
        asyncio.run(service.reorder_news_events(None, NewsEventReorderRequest(news_event_ids=[3, 1, 3])))


def test_reorder_needs_at_least_one_id():
    with pytest.raises(PydanticValidationError):
        NewsEventReorderRequest(news_event_ids=[])


def test_position_is_not_editable_from_the_post_form():
    assert "position" not in NewsEventCreate.model_fields


def test_website_order_is_position_then_newest():
    compiled = [str(clause.compile(dialect=postgresql.dialect())) for clause in WEBSITE_ORDER]
    assert compiled[0] == "news_events.position ASC"
    assert compiled[1] == "news_events.published_on DESC NULLS LAST"


class _FakeSession:
    def __init__(self):
        self.statements = []
        self.committed = False

    async def execute(self, statement):
        self.statements.append(statement)

    async def commit(self):
        self.committed = True


def test_reorder_sets_positions_in_the_given_order():
    session = _FakeSession()
    asyncio.run(NewsEventRepository(session).reorder([7, 2, 9]))
    sql = str(session.statements[0].compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "WHEN 7 THEN 1" in sql and "WHEN 2 THEN 2" in sql and "WHEN 9 THEN 3" in sql
    assert session.committed


def test_unlisted_posts_are_kept_after_the_listed_ones(monkeypatch):
    calls = {}

    async def ids_in_website_order(self):
        return [5, 4, 3, 2, 1]

    async def reorder(self, ordered_ids):
        calls["ids"] = ordered_ids

    monkeypatch.setattr(NewsEventRepository, "ids_in_website_order", ids_in_website_order)
    monkeypatch.setattr(NewsEventRepository, "reorder", reorder)
    asyncio.run(service.reorder_news_events(None, NewsEventReorderRequest(news_event_ids=[2, 5])))
    assert calls["ids"] == [2, 5, 4, 3, 1]


def test_reorder_rejects_unknown_ids(monkeypatch):
    from app.core.errors import NotFoundError

    async def ids_in_website_order(self):
        return [1, 2]

    monkeypatch.setattr(NewsEventRepository, "ids_in_website_order", ids_in_website_order)
    with pytest.raises(NotFoundError):
        asyncio.run(service.reorder_news_events(None, NewsEventReorderRequest(news_event_ids=[1, 99])))
