"""
backend/routers/tasks.py — Task API Routes

POST /run-task       — Submit new debugging task
GET  /task-status/{task_id} — Get task status + progress  
GET  /tasks          — List all tasks (paginated)
POST /tasks/{task_id}/cancel — Cancel a running task
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, HttpUrl
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import Task, TaskStatus, TaskLog
from backend.worker import enqueue_task

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class RunTaskRequest(BaseModel):
    repo_url: str
    issue_id: Optional[int] = None
    issue_description: Optional[str] = None   # Manual override if no issue_id


class TaskResponse(BaseModel):
    task_id: str
    repo_url: str
    issue_id: Optional[int]
    issue_title: Optional[str]
    status: str
    current_node: Optional[str]
    iteration: int
    pr_url: Optional[str]
    pr_number: Optional[int]
    branch_name: Optional[str]
    root_cause: Optional[str]
    fix_summary: Optional[str]
    error_message: Optional[str]
    created_at: Optional[datetime]
    completed_at: Optional[datetime]
    duration_secs: Optional[float]

    class Config:
        from_attributes = True


class TaskListResponse(BaseModel):
    tasks: list[TaskResponse]
    total: int
    page: int
    page_size: int


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/run-task", response_model=TaskResponse, status_code=201)
async def run_task(
    request: RunTaskRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """
    Submit a new debugging task.
    
    The task is created immediately in PENDING state, then the agent
    graph runs asynchronously via the background worker.
    """
    if not request.issue_id and not request.issue_description:
        raise HTTPException(
            status_code=400,
            detail="Must provide either issue_id or issue_description",
        )

    # Create task record
    task = Task(
        id=uuid.uuid4(),
        repo_url=str(request.repo_url),
        issue_id=request.issue_id,
        issue_body=request.issue_description,
        status=TaskStatus.PENDING,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    # Dispatch to background worker (Redis queue)
    await enqueue_task(str(task.id), str(request.repo_url),
                       request.issue_id, request.issue_description)

    return task


@router.get("/task-status/{task_id}", response_model=TaskResponse)
async def get_task_status(
    task_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Return current status and all output for a task."""
    task_uuid = uuid.UUID(task_id)
    task = await db.get(Task, task_uuid)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return task


@router.get("/tasks", response_model=TaskListResponse)
async def list_tasks(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """List all tasks with optional status filter."""
    query = select(Task).order_by(desc(Task.created_at))
    if status:
        try:
            query = query.where(Task.status == TaskStatus(status))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")

    # Count total
    from sqlalchemy import func
    count_result = await db.execute(select(func.count()).select_from(Task))
    total = count_result.scalar()

    # Paginate
    result = await db.execute(query.offset((page - 1) * page_size).limit(page_size))
    tasks  = result.scalars().all()

    return TaskListResponse(
        tasks=[TaskResponse.model_validate(t) for t in tasks],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/tasks/{task_id}/cancel", response_model=TaskResponse)
async def cancel_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Cancel a running task and clean up its sandbox container."""
    task_uuid = uuid.UUID(task_id)
    task = await db.get(Task, task_uuid)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
        raise HTTPException(
            status_code=400,
            detail=f"Task is already in terminal state: {task.status}",
        )

    # Cleanup sandbox if exists
    if task.container_id:
        from sandbox.manager import SandboxManager
        try:
            await SandboxManager().destroy(task.container_id)
        except Exception as e:
            pass  # Best-effort cleanup

    task.status       = TaskStatus.CANCELLED
    task.completed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(task)

    return task
