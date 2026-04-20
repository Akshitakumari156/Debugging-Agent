"""
agent/nodes/reflector.py — Reflection Node

When the fix fails validation, this node:
  1. Analyzes WHY the fix didn't work
  2. Provides targeted notes to guide the next iteration
  3. Decides: retry (if under max iterations) or fail
"""

from __future__ import annotations

import logging
import uuid

from langchain_core.messages import HumanMessage, SystemMessage
from agent.llm import get_llm as _get_llm

from agent.state import AgentState
from backend.config import settings
from db.database import db_session
from db.models import Task, TaskLog, LogLevel
from backend.ws_manager import ws_manager

logger = logging.getLogger(__name__)

REFLECT_SYSTEM_PROMPT = """You are a senior software engineer reviewing a failed bug fix attempt.

Analyze:
1. What the fix tried to do
2. Why the tests still fail
3. What was missed or done incorrectly
4. Give SPECIFIC guidance for the next attempt

Be concise and actionable. Your notes will be passed directly to the next fix attempt."""

REFLECT_HUMAN_TEMPLATE = """Bug Description: {issue_title}

Root cause identified (previous attempt): {root_cause}

Fix applied to files: {files_modified}

Test output after fix:
{test_output}

Iteration: {iteration} / {max_iterations}

What went wrong and how should the next iteration fix it?"""


async def reflector_node(state: AgentState) -> AgentState:
    """
    Reflection Node.
    
    Analyzes a failed fix and prepares targeted guidance for the next
    FixGenerator iteration. Increments the iteration counter.
    Marks should_retry=False once we've hit the max iteration cap.
    """
    task_id   = state["task_id"]
    iteration = state.get("iteration", 0)
    logger.info(f"[Reflector] Analyzing failed fix (iteration {iteration})")

    async with db_session() as db:
        task_uuid = uuid.UUID(task_id)
        task = await db.get(Task, task_uuid)
        task.current_node = "reflector"
        await db.commit()

        try:
            # Check if we've exhausted retries
            if iteration >= settings.MAX_FIX_ITERATIONS - 1:
                await _log(db, task_id, "reflector", LogLevel.WARNING,
                           f"Max iterations ({settings.MAX_FIX_ITERATIONS}) reached. Stopping.")
                return {
                    **state,
                    "should_retry": False,
                    "is_done": False,
                    "error": "Max fix iterations reached without passing tests",
                }

            # -----------------------------------------------------------------
            # LLM reflection
            # -----------------------------------------------------------------
            llm = _get_llm()
            messages = [
                SystemMessage(content=REFLECT_SYSTEM_PROMPT),
                HumanMessage(content=REFLECT_HUMAN_TEMPLATE.format(
                    issue_title=state.get("issue_title", ""),
                    root_cause=state.get("root_cause", "")[:1000],
                    files_modified=", ".join(state.get("files_modified", [])),
                    test_output=state.get("test_output", "")[:2000],
                    iteration=iteration + 1,
                    max_iterations=settings.MAX_FIX_ITERATIONS,
                )),
            ]

            response = await llm.ainvoke(messages)
            reflection_notes = response.content.strip()

            await _log(db, task_id, "reflector", LogLevel.INFO,
                       f"Reflection complete (next iteration: {iteration + 1})",
                       {"notes": reflection_notes})

            return {
                **state,
                "iteration":        iteration + 1,
                "reflection_notes": reflection_notes,
                "should_retry":     True,
            }

        except Exception as exc:
            logger.exception(f"[Reflector] Failed: {exc}")
            await _log(db, task_id, "reflector", LogLevel.ERROR, str(exc))
            return {**state, "should_retry": False, "error": f"Reflector failed: {exc}"}


# _get_llm is imported from agent.llm


async def _log(db, task_id, node, level, message, log_metadata=None):
    db.add(TaskLog(task_id=task_id, level=level, node=node,
                   message=message, log_metadata=log_metadata))
    await db.commit()
    await ws_manager.send_log(str(task_id), level.value, node, message, log_metadata)
