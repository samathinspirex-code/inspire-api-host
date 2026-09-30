import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.core.errors import APIError
from app.modules.site_assistant import service
from app.modules.site_assistant.schemas import SiteAssistantChatRequest

COURSES = [
    {
        "title": "HND in Software Engineering",
        "slug": "hnd-software-engineering",
        "programme_name": "HND",
        "school_name": "School of Computing",
        "awarding_body": "ATHE",
        "code": "HND-SE",
        "blurb": "Build real software.",
        "entry_requirements": "A/L or Foundation",
        "progression_route": "Top-Up Degree",
        "study_options": [
            {"study_mode": "full_time", "price": 450000, "duration": "12 months", "is_enabled": True},
            {"study_mode": "part_time", "price": 380000, "duration": "18 months", "is_enabled": True},
            {"study_mode": "weekend", "price": 1, "duration": "1 day", "is_enabled": False},
        ],
        "topics": [{"topic": "Programming"}],
        "outcomes": [{"outcome": "Ship a web app"}],
    },
    {"title": "AI Mastery", "slug": "ai-mastery", "study_options": []},
]


def test_course_slug_matches_course_pages_only():
    assert service.course_slug("/programs/HND-Software-Engineering") == "hnd-software-engineering"
    assert service.course_slug("/programs/ai-mastery?tab=fees") == "ai-mastery"
    assert service.course_slug("/programs") is None
    assert service.course_slug("/about") is None


def test_course_detail_lists_enabled_fees_only():
    detail = service.format_course_detail(COURSES[0])
    assert "Full time: LKR 450,000, duration 12 months" in detail
    assert "Part time: LKR 380,000, duration 18 months" in detail
    assert "1 day" not in detail
    assert "- Programming" in detail and "- Ship a web app" in detail


def test_catalogue_shows_lowest_fee_and_missing_fee():
    catalogue = service.format_catalogue(COURSES)
    assert "from LKR 380,000" in catalogue
    assert "AI Mastery" in catalogue and "fee not listed" in catalogue


def test_context_includes_current_course_record_on_course_page():
    payload = SiteAssistantChatRequest(message="How much?", path="/programs/hnd-software-engineering", page_text="  Some   page\ntext ")
    with patch.object(service.academic_service, "list_courses", AsyncMock(return_value=COURSES)):
        context = asyncio.run(service.build_context(AsyncMock(), payload))
    assert "CURRENT COURSE" in context and "LKR 450,000" in context
    assert "Visible text: Some page text" in context


def test_context_skips_course_record_elsewhere_and_survives_db_errors():
    payload = SiteAssistantChatRequest(message="Hi", path="/about")
    with patch.object(service.academic_service, "list_courses", AsyncMock(side_effect=RuntimeError("db down"))):
        context = asyncio.run(service.build_context(AsyncMock(), payload))
    assert "CURRENT COURSE" not in context
    assert "No courses are currently listed." in context


def test_rate_limit_blocks_after_per_ip_limit():
    service._requests.clear()
    with patch.object(service.settings, "SITE_ASSISTANT_RATE_LIMIT_PER_HOUR", 2):
        service.check_rate_limit("1.2.3.4")
        service.check_rate_limit("1.2.3.4")
        with pytest.raises(APIError) as error:
            service.check_rate_limit("1.2.3.4")
        service.check_rate_limit("5.6.7.8")
    assert error.value.status_code == 429
    service._requests.clear()


def test_chat_without_openai_key_is_unavailable():
    with patch.object(service.settings, "OPENAI_API_KEY", ""):
        with pytest.raises(APIError) as error:
            asyncio.run(service.chat(AsyncMock(), SiteAssistantChatRequest(message="Hi")))
    assert error.value.status_code == 503
