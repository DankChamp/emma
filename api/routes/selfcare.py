"""
Self-Care / Diagnostics API routes.
"""
from fastapi import APIRouter, Depends

from api.deps import get_diagnostics_manager, get_update_manager
from core.selfcare import DiagnosticsManager, UpdateManager

router = APIRouter(prefix="/selfcare", tags=["selfcare"])


@router.get("/diagnostics")
async def run_diagnostics(diagnostics: DiagnosticsManager = Depends(get_diagnostics_manager)):
    return await diagnostics.full_diagnostic()


@router.post("/repair")
async def auto_repair(diagnostics: DiagnosticsManager = Depends(get_diagnostics_manager)):
    return await diagnostics.auto_repair()


@router.get("/updates")
def check_updates(updater: UpdateManager = Depends(get_update_manager)):
    return updater.check_for_updates()


@router.post("/updates/apply")
def apply_updates(updater: UpdateManager = Depends(get_update_manager)):
    return updater.apply_updates()


@router.post("/updates/deps")
def update_dependencies(updater: UpdateManager = Depends(get_update_manager)):
    return updater.update_dependencies()


@router.get("/changelog")
def get_changelog(updater: UpdateManager = Depends(get_update_manager)):
    return updater.get_changelog()


@router.get("/version")
def get_version(updater: UpdateManager = Depends(get_update_manager)):
    return updater.get_version_info()


@router.post("/repair/databases")
def repair_databases(diagnostics: DiagnosticsManager = Depends(get_diagnostics_manager)):
    return diagnostics.repair_databases()


@router.post("/repair/config")
def repair_config(diagnostics: DiagnosticsManager = Depends(get_diagnostics_manager)):
    return diagnostics.repair_config()
