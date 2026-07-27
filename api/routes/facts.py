from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from config import Settings, get_settings
from core.facts import FactRouter, classify_topic

router = APIRouter(prefix="/facts", tags=["facts"])


class FactPush(BaseModel):
    fact: str
    topics: list[str] | None = None


class FactResponse(BaseModel):
    fact: str
    topics: list[str]
    pushed_to: list[str]


@router.post("", response_model=FactResponse)
async def push_fact(payload: FactPush, settings: Settings = Depends(get_settings)):
    fact_router = FactRouter(settings)
    topics = payload.topics or classify_topic(payload.fact)
    result = await fact_router.push_fact(payload.fact, topics)
    return FactResponse(**result)


@router.post("/classify")
def classify_fact(payload: FactPush):
    topics = payload.topics or classify_topic(payload.fact)
    return {"fact": payload.fact, "topics": topics}
