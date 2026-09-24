"""Unit tests for backend.app.knowledge.retriever against the real knowledge docs."""
import tempfile
from pathlib import Path

import pytest

from backend.app.knowledge.retriever import KnowledgeRetriever, load_chunks, slugify, query_for_incident

pytestmark = pytest.mark.unit

DOCS_DIR = "backend/app/knowledge/docs"


def test_chunk_ids_stable_across_two_loads():
    a = load_chunks(DOCS_DIR)
    b = load_chunks(DOCS_DIR)
    assert [c.chunk_id for c in a] == [c.chunk_id for c in b]


def test_chunk_ids_unique():
    chunks = load_chunks(DOCS_DIR)
    ids = [c.chunk_id for c in chunks]
    assert len(ids) == len(set(ids))
    assert len(chunks) >= 10 * 4  # ~10 docs, several sections each


def test_at_least_ten_docs_loaded():
    chunks = load_chunks(DOCS_DIR)
    doc_ids = {c.doc_id for c in chunks}
    assert len(doc_ids) >= 10


def test_duplicate_chunk_id_raises():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "dup.md"
        path.write_text(
            "---\ndoc_id: dup\ntitle: Dup\ntags: []\n---\n\n## Same Heading\ntext a\n\n## Same Heading\ntext b\n"
        )
        with pytest.raises(ValueError, match="Duplicate"):
            load_chunks(tmp)


def test_search_seatbelt_query_top_result_is_seatbelt_doc():
    retriever = KnowledgeRetriever.from_directory(DOCS_DIR)
    results = retriever.search("seatbelt unfastened working", tags={"SEATBELT_VIOLATION"}, k=3)
    assert results
    assert results[0].chunk.doc_id == "seatbelt-safety"


def test_search_is_deterministic_across_calls():
    retriever = KnowledgeRetriever.from_directory(DOCS_DIR)
    r1 = retriever.search("proximity breach zone", tags={"PROXIMITY_BREACH"}, k=5)
    r2 = retriever.search("proximity breach zone", tags={"PROXIMITY_BREACH"}, k=5)
    assert [sc.chunk.chunk_id for sc in r1] == [sc.chunk.chunk_id for sc in r2]


def test_tag_match_boosts_score():
    retriever = KnowledgeRetriever.from_directory(DOCS_DIR)
    no_tag = retriever.search("idle deviation operator", tags=set(), k=10)
    with_tag = retriever.search("idle deviation operator", tags={"IDLE_DEVIATION"}, k=10)
    no_tag_score = next(sc.score for sc in no_tag if sc.chunk.doc_id == "idle-management")
    with_tag_score = next(sc.score for sc in with_tag if sc.chunk.doc_id == "idle-management")
    assert with_tag_score > no_tag_score


def test_get_chunk_by_id():
    retriever = KnowledgeRetriever.from_directory(DOCS_DIR)
    chunk = retriever.get("seatbelt-safety#overview")
    assert chunk is not None
    assert chunk.title == "Seatbelt Safety"


def test_get_unknown_chunk_returns_none():
    retriever = KnowledgeRetriever.from_directory(DOCS_DIR)
    assert retriever.get("not-a-real-chunk#nope") is None


def test_slugify():
    assert slugify("Ingress And Egress Grace") == "ingress-and-egress-grace"
    assert slugify("  Multiple   Spaces  ") == "multiple-spaces"


def test_query_for_incident_extracts_tags_and_context():
    packet = {
        "timeline": [
            {"event_type": "SEATBELT_VIOLATION", "summary": "Seatbelt unfastened while WORKING"},
            {"event_type": "PROXIMITY_BREACH", "summary": "Proximity breach in ORANGE zone"},
        ],
        "context": {"weather": "RAIN", "ground": "MUDDY"},
    }
    query, tags = query_for_incident(packet)
    assert tags == {"SEATBELT_VIOLATION", "PROXIMITY_BREACH"}
    assert "RAIN" in query and "MUDDY" in query
