from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.deps import get_project_manager
from core.tasks.project_manager import ProjectManager

router = APIRouter(prefix="/projects", tags=["projects"])


# --- Pydantic models ---

class ProjectCreate(BaseModel):
    name: str
    description: str = ""
    deadline: Optional[str] = None
    priority: str = "medium"


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    progress: Optional[float] = None
    status: Optional[str] = None
    deadline: Optional[str] = None
    priority: Optional[str] = None


class MilestoneCreate(BaseModel):
    project_id: int
    title: str
    deadline: Optional[str] = None


class StudyLogCreate(BaseModel):
    subject: str
    hours: float
    notes: str = ""
    date: Optional[str] = None


# --- Project endpoints ---

@router.get("")
def list_projects(status: Optional[str] = None,
                  pm: ProjectManager = Depends(get_project_manager)):
    return pm.list_projects(status=status)


@router.get("/{project_id}")
def get_project(project_id: int, pm: ProjectManager = Depends(get_project_manager)):
    p = pm.get_project(project_id)
    if not p:
        raise HTTPException(404, "Project not found")
    return p


@router.post("")
def create_project(payload: ProjectCreate,
                   pm: ProjectManager = Depends(get_project_manager)):
    return pm.create_project(payload.name, description=payload.description,
                             deadline=payload.deadline, priority=payload.priority)


@router.patch("/{project_id}")
def update_project(project_id: int, payload: ProjectUpdate,
                   pm: ProjectManager = Depends(get_project_manager)):
    updated = pm.update_project(project_id, **payload.model_dump(exclude_none=True))
    if not updated:
        raise HTTPException(404, "Project not found")
    return updated


@router.delete("/{project_id}")
def delete_project(project_id: int, pm: ProjectManager = Depends(get_project_manager)):
    if not pm.delete_project(project_id):
        raise HTTPException(404, "Project not found")
    return {"ok": True}


# --- Milestone endpoints ---

@router.post("/milestones")
def create_milestone(payload: MilestoneCreate,
                     pm: ProjectManager = Depends(get_project_manager)):
    return pm.create_milestone(payload.project_id, payload.title, deadline=payload.deadline)


@router.get("/{project_id}/milestones")
def list_milestones(project_id: int, pm: ProjectManager = Depends(get_project_manager)):
    return pm.list_milestones(project_id)


@router.post("/milestones/{milestone_id}/done")
def complete_milestone(milestone_id: int,
                       pm: ProjectManager = Depends(get_project_manager)):
    m = pm.set_milestone_done(milestone_id, True)
    if not m:
        raise HTTPException(404, "Milestone not found")
    return m


@router.post("/milestones/{milestone_id}/reopen")
def reopen_milestone(milestone_id: int,
                     pm: ProjectManager = Depends(get_project_manager)):
    m = pm.set_milestone_done(milestone_id, False)
    if not m:
        raise HTTPException(404, "Milestone not found")
    return m


@router.delete("/milestones/{milestone_id}")
def delete_milestone(milestone_id: int,
                     pm: ProjectManager = Depends(get_project_manager)):
    if not pm.delete_milestone(milestone_id):
        raise HTTPException(404, "Milestone not found")
    return {"ok": True}


# --- Study log endpoints ---

@router.get("/study")
def list_study(subject: Optional[str] = None, days: Optional[int] = None,
               pm: ProjectManager = Depends(get_project_manager)):
    return pm.list_study_logs(subject=subject, days=days)


@router.get("/study/summary")
def study_summary(days: int = 7, pm: ProjectManager = Depends(get_project_manager)):
    return {"summary": pm.study_summary(days=days)}


@router.post("/study")
def log_study(payload: StudyLogCreate,
              pm: ProjectManager = Depends(get_project_manager)):
    day = date.fromisoformat(payload.date) if payload.date else None
    return pm.log_study(payload.subject, payload.hours, notes=payload.notes, day=day)


@router.delete("/study/{log_id}")
def delete_study(log_id: int, pm: ProjectManager = Depends(get_project_manager)):
    if not pm.delete_study_log(log_id):
        raise HTTPException(404, "Study log not found")
    return {"ok": True}
