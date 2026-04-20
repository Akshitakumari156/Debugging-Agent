"""
backend/routers/logs.py — Log Streaming Routes

GET /logs/{task_id}         — Return all logs for a task
GET /logs/{task_id}/stream  — SSE stream of new log entries
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, asc
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import Task, TaskLog

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class LogEntry(BaseModel):
    id: uuid.UUID
    task_id: uuid.UUID
    level: str
    node: Optional[str]
    message: str
    log_metadata: Optional[dict] = None
    created_at: datetime

    class Config:
        from_attributes = True


class LogsResponse(BaseModel):
    task_id: str
    logs: list[LogEntry]
    total: int


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/logs/{task_id}", response_model=LogsResponse)
async def get_logs(
    task_id: str,
    level: Optional[str] = Query(None, description="Filter by log level"),
    node: Optional[str]  = Query(None, description="Filter by agent node"),
    limit: int           = Query(500, ge=1, le=5000),
    db: AsyncSession     = Depends(get_db),
):
    """
    Return all log entries for a task.
    
    Supports filtering by level (info/warning/error) and node name.
    """
    # Verify task exists
    task_uuid = uuid.UUID(task_id)
    task = await db.get(Task, task_uuid)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    query = (
        select(TaskLog)
        .where(TaskLog.task_id == task_uuid)
        .order_by(asc(TaskLog.created_at))
        .limit(limit)
    )

    if level:
        from db.models import LogLevel
        try:
            query = query.where(TaskLog.level == LogLevel(level))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid level: {level}")

    if node:
        query = query.where(TaskLog.node == node)

    result = await db.execute(query)
    logs   = result.scalars().all()

    return LogsResponse(
        task_id=task_id,
        logs=[LogEntry.model_validate(l) for l in logs],
        total=len(logs),
    )


@router.get("/logs/{task_id}/stream")
async def stream_logs(
    task_id: str,
    after_id: Optional[str] = Query(None, description="Stream logs after this log ID"),
    db: AsyncSession        = Depends(get_db),
):
    """
    Server-Sent Events (SSE) stream of log entries.
    
    Clients receive new log entries as they are written to the DB.
    Terminates when the task reaches a terminal state.
    """
    task_uuid = uuid.UUID(task_id)
    task = await db.get(Task, task_uuid)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    async def event_generator():
        from db.models import TaskStatus
        last_id = after_id
        terminal_states = {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }

        while True:
            # Re-query for new logs since last_id
            async with get_db() as fresh_db:
                query = (
                    select(TaskLog)
                    .where(TaskLog.task_id == task_uuid)
                    .order_by(asc(TaskLog.created_at))
                )
                if last_id:
                    # Get logs after this ID (by created_at ordering)
                    last_uuid = uuid.UUID(last_id)
                    query = query.where(TaskLog.id > last_uuid)

                result = await fresh_db.execute(query.limit(50))
                new_logs = result.scalars().all()

                for log in new_logs:
                    payload = json.dumps({
                        "id":         str(log.id),
                        "level":      log.level.value,
                        "node":       log.node,
                        "message":    log.message,
                        "log_metadata": log.log_metadata,
                        "created_at": log.created_at.isoformat(),
                    })
                    yield f"data: {payload}\n\n"
                    last_id = str(log.id)

                # Check if task is done
                fresh_task = await fresh_db.get(Task, task_uuid)
                if fresh_task and fresh_task.status in terminal_states:
                    yield f"data: {json.dumps({'event': 'done', 'status': fresh_task.status.value})}\n\n"
                    return

            await asyncio.sleep(1.0)  # Poll every 1 second

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )

