"""GET /knowledge/chunks/{chunk_id} — used by the frontend to render citation chips."""
from fastapi import APIRouter, Depends, HTTPException, status

from backend.app.api.deps import get_current_user
from backend.app.config import settings
from backend.app.db.models import User
from backend.app.knowledge.retriever import KnowledgeRetriever

router = APIRouter()

_retriever: KnowledgeRetriever | None = None


def get_retriever() -> KnowledgeRetriever:
    global _retriever
    if _retriever is None:
        _retriever = KnowledgeRetriever.from_directory(settings.knowledge_dir)
    return _retriever


@router.get("/chunks/{chunk_id:path}")
async def get_knowledge_chunk(chunk_id: str, user: User = Depends(get_current_user)):
    retriever = get_retriever()
    chunk = retriever.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge chunk not found")
    return {
        "chunk_id": chunk.chunk_id, "doc_id": chunk.doc_id, "title": chunk.title,
        "heading": chunk.heading, "text": chunk.text,
    }
