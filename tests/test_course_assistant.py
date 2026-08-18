from types import SimpleNamespace

from app.modules.lms.assistant_service import chunk_text, rank_chunks, rank_sources


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
