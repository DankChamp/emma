from fastapi import APIRouter, Depends

from api.deps import get_luna_manager, get_memory_manager
from core.memory import MemoryManager

router = APIRouter(prefix="/luna", tags=["luna"])


@router.get("/health")
async def luna_health():
    from main import luna_mgr

    if luna_mgr:
        h = await luna_mgr.health()
        return {"ok": True, **h}
    return {"ok": False, "configured": False}


@router.post("/launch")
async def luna_launch():
    from main import luna_mgr

    if luna_mgr:
        ok = await luna_mgr.launch()
        return {"ok": ok}
    return {"ok": False, "error": "Luna not configured"}


@router.post("/stop")
async def luna_stop():
    from main import luna_mgr

    if luna_mgr:
        luna_mgr.stop()
        return {"ok": True}
    return {"ok": False, "error": "Luna not configured"}
