from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.deps import get_memory_manager
from core.memory import MemoryManager

router = APIRouter(prefix="/ingest", tags=["ingest"])


class ResearchSummary(BaseModel):
    item_type: str
    title: str
    summary: str
    tags: list[str] = []


@router.post("/research-summary")
def ingest_research_summary(
    payload: ResearchSummary,
    memory: MemoryManager = Depends(get_memory_manager),
):
    entry = (
        f"[{payload.item_type}] {payload.title}: {payload.summary}"
        + (f" (tags: {', '.join(payload.tags)})" if payload.tags else "")
    )
    existing = memory.get_long_term_text()
    updated = (existing + "\n" + entry) if existing else entry
    memory.set_long_term_text(updated)
    return {"ok": True, "entry": entry}
