from types import SimpleNamespace

from app.modules.lms.assistant_service import (
    chunk_text,
    normalize_pdf_download_url,
    parse_vimeo_vtt,
    rank_chunks,
    rank_sources,
)


def source(source_id, title, content):
    return SimpleNamespace(knowledge_source_id=source_id, title=title, content=content)


def test_rank_sources_prefers_title_matches():
    sources = [
        source(1, "General introduction", "Python variables store values."),
        source(2, "Python variables", "A variable is created when a value is assigned."),
    ]
    assert [item.knowledge_source_id for item in rank_sources("What are Python variables?", sources)] == [2, 1]


def test_rank_sources_returns_no_unrelated_material():
    sources = [source(1, "Programming", "Variables and functions")]
    assert rank_sources("Explain accounting ledgers", sources) == []


def test_chunk_text_splits_long_content_with_searchable_overlap():
    text = "First topic explains variables. " * 70
    chunks = chunk_text(text, max_chars=300, overlap=40)
    assert len(chunks) > 2
    assert all(len(chunk) <= 310 for chunk in chunks)


def test_summary_question_retrieves_early_course_passages():
    source_item = SimpleNamespace(knowledge_source_id=1, title="Programming guide")
    rows = [
        (SimpleNamespace(content="Introduction to structured programming.", position=1), source_item),
        (SimpleNamespace(content="Functions and variables are covered next.", position=2), source_item),
    ]
    matches = rank_chunks("Can you summarize the content?", rows)
    assert [chunk.position for chunk, _source in matches] == [1, 2]


def test_google_drive_pdf_share_link_becomes_direct_download():
    result = normalize_pdf_download_url(
        "https://drive.google.com/file/d/1AbC_def-234/view?usp=sharing"
    )
    assert result == (
        "https://drive.usercontent.google.com/download"
        "?id=1AbC_def-234&export=download&confirm=t"
    )


def test_non_drive_pdf_url_is_unchanged():
    url = "https://cdn.example.edu/course/lesson.pdf"
    assert normalize_pdf_download_url(url) == url


def test_summary_with_for_now_still_retrieves_course_passages():
    source_item = SimpleNamespace(knowledge_source_id=1, title="Data warehousing")
    rows = [
        (SimpleNamespace(content="A warehouse integrates historical data for analysis.", position=1), source_item),
        (SimpleNamespace(content="Data marts serve focused business teams.", position=2), source_item),
    ]
    matches = rank_chunks("Can you give me a summary of the content for now?", rows)
    assert [chunk.position for chunk, _source in matches] == [1, 2]


def test_vimeo_vtt_is_grouped_with_timestamps():
    transcript = """WEBVTT

00:00:01.000 --> 00:00:04.500
Welcome to the course.

00:00:05.000 --> 00:00:09.200
Today we introduce data warehousing.
"""
    chunks = parse_vimeo_vtt(transcript)
    assert chunks == [(
        "Welcome to the course. Today we introduce data warehousing.",
        None,
        1,
        10,
    )]


def test_video_question_does_not_mix_in_pdf_passages():
    video = SimpleNamespace(knowledge_source_id=1, title="Lecture video", source_type="video_transcript")
    pdf = SimpleNamespace(knowledge_source_id=2, title="Lecture slides", source_type="pdf")
    rows = [
        (SimpleNamespace(content="The video describes faster uploads and a redesigned profile.", position=1), video),
        (SimpleNamespace(content="The PDF discusses improvements to data integration.", position=1), pdf),
    ]
    matches = rank_chunks("What improvements does the video discuss?", rows)
    assert matches
    assert all(source.source_type == "video_transcript" for _chunk, source in matches)


def test_common_vimeo_misspelling_still_finds_transcript():
    video = SimpleNamespace(knowledge_source_id=1, title="Video", source_type="video_transcript")
    rows = [
        (SimpleNamespace(content="New Vimeo features include faster uploads.", position=1), video),
    ]
    assert rank_chunks("What are the features in vemio?", rows)


def test_broad_lessons_question_returns_course_overview():
    pdf = SimpleNamespace(knowledge_source_id=1, title="Lecture", source_type="pdf")
    rows = [
        (SimpleNamespace(content="Data integration combines multiple sources.", position=1), pdf),
    ]
    assert rank_chunks("What are the lessons taught?", rows)
