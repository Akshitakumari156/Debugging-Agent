"""
backend/worker.py — Background Task Worker

Bridges the FastAPI HTTP layer with the LangGraph agent:
  1. Receives task IDs from a Redis queue
  2. Builds the initial agent state
  3. Calls run_agent() — the full LangGraph graph
  4. Handles cleanup (container removal, DB status updates)
  5. Broadcasts log events via WebSocket manager

Can run as a standalone process: `python -m backend.worker`
"""

from __future__ import annotations

import asyncio
import json
import logging
import traceback
import uuid
from datetime import datetime, timezone

import redis.asyncio as aioredis

from agent.graph import run_agent
from agent.state import AgentState
from backend.config import settings
from backend.ws_manager import ws_manager
from db.database import db_session
from db.models import Task, TaskLog, LogLevel, TaskStatus
from sandbox.manager import SandboxManager

logger = logging.getLogger(__name__)

QUEUE_KEY   = "autodebug:task_queue"
TIMEOUT_KEY = "autodebug:task_timeout:{task_id}"


# ---------------------------------------------------------------------------
# Enqueue — called by the FastAPI route handler
# ---------------------------------------------------------------------------

async def enqueue_task(
    task_id: str,
    repo_url: str,
    issue_id: int | None,
    issue_description: str | None,
) -> None:
    """Push a task to the Redis queue for the worker to process."""
    redis = await _get_redis()
    payload = json.dumps({
        "task_id":           task_id,
        "repo_url":          repo_url,
        "issue_id":          issue_id,
        "issue_description": issue_description,
    })
    await redis.rpush(QUEUE_KEY, payload)
    logger.info(f"[Worker] Enqueued task {task_id}")


# ---------------------------------------------------------------------------
# Worker main loop — run as: python -m backend.worker
# ---------------------------------------------------------------------------

async def worker_loop() -> None:
    """
    Continuously pull tasks from the Redis queue and process them.
    Runs a configurable number of concurrent tasks.
    """
    redis = await _get_redis()
    logger.info("[Worker] Started. Waiting for tasks...")

    while True:
        try:
            # BLPOP — blocks until a task arrives (timeout 5s to allow graceful shutdown)
            item = await redis.blpop(QUEUE_KEY, timeout=5)
            if item is None:
                continue  # Timeout; loop again

            _, raw = item
            payload = json.loads(raw)

            # Process in a separate task (allows worker to pick up next immediately)
            asyncio.create_task(_process_task(payload))

        except Exception as e:
            logger.error(f"[Worker] Error in main loop: {e}")
            await asyncio.sleep(2)


async def _process_task(payload: dict) -> None:
    """
    Full task processing pipeline:
      1. Build initial state
      2. Run LangGraph agent
      3. Cleanup sandbox
      4. Update DB status
    """
    task_id = payload["task_id"]
    logger.info(f"[Worker] Processing task {task_id}")

    start_time = datetime.now(timezone.utc)

    try:
        # Build initial agent state
        initial_state: AgentState = {
            "task_id":           task_id,
            "repo_url":          payload["repo_url"],
            "issue_id":          payload.get("issue_id"),
            "issue_title":       "",
            "issue_body":        payload.get("issue_description") or "",
            "iteration":         0,
            "should_retry":      False,
            "is_done":           False,
        }

        # Run the graph — this is the full agent execution
        final_state = await asyncio.wait_for(
            run_agent(initial_state),
            timeout=settings.SANDBOX_TIMEOUT,
        )

        # Determine final status
        if final_state.get("pr_url"):
            final_status = TaskStatus.COMPLETED
        elif final_state.get("error"):
            final_status = TaskStatus.FAILED
        else:
            final_status = TaskStatus.FAILED  # No PR, no error = unexpected

        duration = (datetime.now(timezone.utc) - start_time).total_seconds()

        async with db_session() as db:
            task_uuid = uuid.UUID(task_id)
            task = await db.get(Task, task_uuid)
            if task:
                task.status           = final_status
                task.completed_at     = datetime.now(timezone.utc)
                task.duration_secs    = duration
                task.error_message    = final_state.get("error")
                await db.commit()

        # Broadcast completion via WebSocket
        await ws_manager.broadcast(task_id, {
            "event":  "task_complete",
            "status": final_status.value,
            "pr_url": final_state.get("pr_url"),
        })

        logger.info(
            f"[Worker] Task {task_id} finished in {duration:.1f}s "
            f"status={final_status.value}"
        )

    except asyncio.TimeoutError:
        logger.error(f"[Worker] Task {task_id} timed out after {settings.SANDBOX_TIMEOUT}s")
        await _mark_failed(task_id, "Task timed out")

    except Exception as exc:
        logger.exception(f"[Worker] Unexpected error for task {task_id}: {exc}")
        await _mark_failed(task_id, f"Unexpected error: {exc}")

    finally:
        # Always attempt sandbox cleanup
        await _cleanup_sandbox(task_id)


async def _mark_failed(task_id: str, error: str) -> None:
    """Mark a task as FAILED in the database."""
    async with db_session() as db:
        task_uuid = uuid.UUID(task_id)
        task = await db.get(Task, task_uuid)
        if task:
            task.status        = TaskStatus.FAILED
            task.completed_at  = datetime.now(timezone.utc)
            task.error_message = error
            await db.commit()


async def _cleanup_sandbox(task_id: str) -> None:
    """Remove the Docker container associated with this task."""
    async with db_session() as db:
        task_uuid = uuid.UUID(task_id)
        task = await db.get(Task, task_uuid)
        if task and task.container_id:
            try:
                await SandboxManager().destroy(task.container_id)
                logger.info(f"[Worker] Sandbox cleaned up for task {task_id}")
            except Exception as e:
                logger.warning(f"[Worker] Sandbox cleanup failed for {task_id}: {e}")


async def _get_redis() -> aioredis.Redis:
    return await aioredis.from_url(settings.REDIS_URL, decode_responses=True)


# ---------------------------------------------------------------------------
# Entrypoint — python -m backend.worker
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    asyncio.run(worker_loop())
