"""
Aqua management routes — start, stop, and check Aqua from the web UI or CLI.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.deps import get_aqua_manager

router = APIRouter(prefix="/aqua", tags=["aqua"])


class AskRequest(BaseModel):
    message: str
    task_type: str = "research"
    provider: Optional[str] = None
    model: Optional[str] = None


@router.get("/status")
async def aqua_status(aqua_mgr=Depends(get_aqua_manager)):
    if aqua_mgr is None:
        return {"configured": False, "message": "Aqua is not configured (set AQUA_PROJECT_DIR)"}
    return await aqua_mgr.health()


@router.post("/launch")
async def aqua_launch(aqua_mgr=Depends(get_aqua_manager)):
    if aqua_mgr is None:
        raise HTTPException(400, "Aqua is not configured")
    ok = await aqua_mgr.launch()
    if not ok:
        raise HTTPException(500, "Failed to launch Aqua")
    return {"ok": True, "status": await aqua_mgr.health()}


@router.post("/stop")
async def aqua_stop(aqua_mgr=Depends(get_aqua_manager)):
    if aqua_mgr is None:
        raise HTTPException(400, "Aqua is not configured")
    aqua_mgr.stop()
    return {"ok": True}


@router.post("/ask")
async def aqua_ask(payload: AskRequest, aqua_mgr=Depends(get_aqua_manager)):
    if aqua_mgr is None:
        raise HTTPException(400, "Aqua is not configured")
    from core.aqua import AquaClient
    from config import get_settings

    settings = get_settings()
    client = AquaClient(settings.aqua_api_url, settings.aqua_api_key)
    reply = await client.chat(
        message=payload.message,
        task_type=payload.task_type,
        provider=payload.provider,
        model=payload.model,
    )
    if reply is None:
        raise HTTPException(503, "Aqua did not respond")
    return {"reply": reply}
