from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.deps import get_task_manager
from core.tasks import TaskManager

router = APIRouter(prefix="/tasks", tags=["tasks"])


class TaskCreate(BaseModel):
    title: str
    project: Optional[str] = None
    priority: str = "medium"
    deadline: Optional[str] = None  # ISO date "2026-07-21" or datetime


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    project: Optional[str] = None
    priority: Optional[str] = None
    deadline: Optional[str] = None


@router.get("")
def list_tasks(status: Optional[str] = None, project: Optional[str] = None,
               tasks: TaskManager = Depends(get_task_manager)):
    return tasks.list(status=status, project=project)


@router.get("/counts")
def task_counts(tasks: TaskManager = Depends(get_task_manager)):
    return tasks.counts()


@router.post("")
def create_task(payload: TaskCreate, tasks: TaskManager = Depends(get_task_manager)):
    if not payload.title.strip():
        raise HTTPException(400, "Task title is required")
    return tasks.create(payload.title, project=payload.project,
                        priority=payload.priority, deadline=payload.deadline)


@router.patch("/{task_id}")
def update_task(task_id: int, payload: TaskUpdate,
                tasks: TaskManager = Depends(get_task_manager)):
    updated = tasks.update(task_id, title=payload.title, project=payload.project,
                           priority=payload.priority, deadline=payload.deadline)
    if not updated:
        raise HTTPException(404, f"No task with id {task_id}")
    return updated


@router.post("/{task_id}/done")
def complete_task(task_id: int, tasks: TaskManager = Depends(get_task_manager)):
    updated = tasks.set_done(task_id, True)
    if not updated:
        raise HTTPException(404, f"No task with id {task_id}")
    return updated


@router.post("/{task_id}/reopen")
def reopen_task(task_id: int, tasks: TaskManager = Depends(get_task_manager)):
    updated = tasks.set_done(task_id, False)
    if not updated:
        raise HTTPException(404, f"No task with id {task_id}")
    return updated


@router.delete("/done")
def clear_done(tasks: TaskManager = Depends(get_task_manager)):
    return {"ok": True, "deleted": tasks.clear_done()}


@router.delete("/{task_id}")
def delete_task(task_id: int, tasks: TaskManager = Depends(get_task_manager)):
    if not tasks.delete(task_id):
        raise HTTPException(404, f"No task with id {task_id}")
    return {"ok": True}
