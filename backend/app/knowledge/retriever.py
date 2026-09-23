"""
Lightweight BM25 knowledge retrieval over the markdown docs in
backend/app/knowledge/docs/ (Stage 3 Batch 3G). Each doc is chunked by `##`
heading; chunk_id = f"{doc_id}#{slugify(heading)}" is stable across
reloads as long as headings don't change text, which is what the cold
worker's grounding validator relies on to check that a knowledge_ref the
LLM cites actually exists.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from rank_bm25 import BM25Okapi


def slugify(text: str) -> str:
    slug = text.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


@dataclass
class KnowledgeChunk:
    chunk_id: str
    doc_id: str
    title: str
    heading: str
    text: str
    tags: list[str]


@dataclass
class ScoredChunk:
    chunk: KnowledgeChunk
    score: float


def _parse_front_matter(raw: str) -> tuple[dict, str]:
    """Splits a doc into (front_matter_dict, body). Front matter is the
    '---\\n...\\n---' block at the top, YAML-parsed."""
    if not raw.startswith("---"):
        return {}, raw
    parts = raw.split("---", 2)
    if len(parts) < 3:
        return {}, raw
    front_matter = yaml.safe_load(parts[1]) or {}
    body = parts[2]
    return front_matter, body


def load_chunks(directory: str) -> list[KnowledgeChunk]:
    chunks: list[KnowledgeChunk] = []
    seen_ids: set[str] = set()

    for path in sorted(Path(directory).glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        front_matter, body = _parse_front_matter(raw)
        doc_id = front_matter.get("doc_id", path.stem)
        title = front_matter.get("title", doc_id)
        tags = front_matter.get("tags", [])

        # Split on level-2 headings ("## Heading"); keep the heading text
        # with each section.
        sections = re.split(r"^##\s+(.+)$", body, flags=re.MULTILINE)
        # sections[0] is text before the first "##" (usually empty/whitespace)
        for i in range(1, len(sections), 2):
            heading = sections[i].strip()
            text = sections[i + 1].strip() if i + 1 < len(sections) else ""
            chunk_id = f"{doc_id}#{slugify(heading)}"
            if chunk_id in seen_ids:
                raise ValueError(f"Duplicate knowledge chunk_id: {chunk_id} (in {path})")
            seen_ids.add(chunk_id)
            chunks.append(KnowledgeChunk(
                chunk_id=chunk_id, doc_id=doc_id, title=title, heading=heading,
                text=text, tags=list(tags),
            ))

    return chunks


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


class KnowledgeRetriever:
    def __init__(self, chunks: list[KnowledgeChunk]):
        self.chunks = chunks
        self._corpus_tokens = [_tokenize(f"{c.heading} {c.text}") for c in chunks]
        self._bm25 = BM25Okapi(self._corpus_tokens) if chunks else None

    @classmethod
    def from_directory(cls, directory: str) -> "KnowledgeRetriever":
        return cls(load_chunks(directory))

    def search(self, query: str, tags: set[str] | None = None, k: int = 5) -> list[ScoredChunk]:
        if not self.chunks or self._bm25 is None:
            return []
        tags = tags or set()
        query_tokens = _tokenize(query)
        raw_scores = self._bm25.get_scores(query_tokens)

        scored = []
        for chunk, score in zip(self.chunks, raw_scores):
            boosted = float(score) * (1.5 if tags & set(chunk.tags) else 1.0)
            scored.append(ScoredChunk(chunk=chunk, score=boosted))

        # Deterministic ordering for ties: score desc, then chunk_id asc.
        scored.sort(key=lambda sc: (-sc.score, sc.chunk.chunk_id))
        return scored[:k]

    def get(self, chunk_id: str) -> KnowledgeChunk | None:
        for c in self.chunks:
            if c.chunk_id == chunk_id:
                return c
        return None


def query_for_incident(packet: dict) -> tuple[str, set[str]]:
    """
    Builds a retrieval query + tag set from an incident packet (as built by
    genai.packet.build_incident_packet): tags are the event types present
    in the incident's timeline; the query is the timeline summaries plus
    context words (weather, ground), which is what most naturally overlaps
    with the knowledge docs' own wording.
    """
    tags = {entry.get("event_type") for entry in packet.get("timeline", []) if entry.get("event_type")}
    query_parts = [entry.get("summary", "") for entry in packet.get("timeline", [])]
    context = packet.get("context") or {}
    if context.get("weather"):
        query_parts.append(str(context["weather"]))
    if context.get("ground"):
        query_parts.append(str(context["ground"]))
    return " ".join(query_parts), tags
