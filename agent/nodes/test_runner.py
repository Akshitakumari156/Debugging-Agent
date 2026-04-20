"""
agent/nodes/test_runner.py — Test Runner Node

Responsibilities:
  1. Run the full test suite in the sandbox
  2. Parse results (passed / failed counts)
  3. Set validation_success flag for graph routing
"""

from __future__ import annotations

import logging
import re
import uuid

from sqlalchemy.orm import selectinload

from agent.state import AgentState
from db.database import db_session
from db.models import Task, TaskLog, LogLevel, TaskStatus, FixAttempt, FixStatus
from sandbox.executor import SandboxExecutor
from backend.ws_manager import ws_manager

logger = logging.getLogger(__name__)


async def test_runner_node(state: AgentState) -> AgentState:
    """
    Test Runner Node.
    
    Executes the full test suite and determines whether the fix:
    a) Resolved the original bug (previously failing tests now pass)
    b) Introduced any regressions (previously passing tests now fail)
    """
    task_id = state["task_id"]
    logger.info(f"[TestRunner] Starting for task {task_id}")

    async with db_session() as db:
        task_uuid = uuid.UUID(task_id)
        task = await db.get(Task, task_uuid, options=[selectinload(Task.fix_attempts)])
        task.status = TaskStatus.TESTING
        task.current_node = "test_runner"
        await db.commit()

        try:
            executor  = SandboxExecutor(state["container_id"])
            workspace = state["workspace_path"]
            test_cmd  = state["test_command"]

            await _log(db, task_id, "test_runner", LogLevel.INFO,
                       f"Running test suite: {test_cmd}")

            result = await executor.run(
                f"cd {workspace} && {test_cmd}", timeout=120
            )
            test_output = result.output

            # -----------------------------------------------------------------
            # Parse test counts
            # -----------------------------------------------------------------
            passed, failed = _parse_test_counts(test_output, state["language"])
            
            # If we successfully parsed counts, trust them over the exit code
            # (sometimes tools return non-zero for warnings)
            if passed > 0 or failed > 0:
                validation_success = (failed == 0)
            else:
                validation_success = (result.exit_code == 0)

            await _log(db, task_id, "test_runner", LogLevel.INFO,
                       f"Tests: {passed} passed, {failed} failed. "
                       f"Validation: {'✅ SUCCESS' if validation_success else '❌ FAILED'}",
                       {"test_output": test_output[:2000]})

            # -----------------------------------------------------------------
            # Update fix attempt record
            # -----------------------------------------------------------------
            attempts = task.fix_attempts
            # Find the attempt for this iteration
            for attempt in attempts:
                if attempt.iteration == state.get("iteration", 0):
                    attempt.status       = FixStatus.PASSED if validation_success else FixStatus.FAILED
                    attempt.test_output  = test_output
                    attempt.tests_passed = passed
                    attempt.tests_failed = failed
                    if not validation_success:
                        # Extract error for reflector
                        from agent.nodes.bug_reproducer import _extract_error_trace
                        attempt.error_message = _extract_error_trace(test_output)
                    break
            await db.commit()

            return {
                **state,
                "test_output":        test_output,
                "tests_passed":       passed,
                "tests_failed":       failed,
                "validation_success": validation_success,
            }

        except Exception as exc:
            logger.exception(f"[TestRunner] Failed: {exc}")
            await _log(db, task_id, "test_runner", LogLevel.ERROR, str(exc))
            return {**state, "error": f"TestRunner failed: {exc}", "validation_success": False}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_test_counts(output: str, language: str) -> tuple[int, int]:
    """Extract passed/failed counts from test output."""
    passed, failed = 0, 0

    if language == "python":
        # pytest summary: "3 passed, 1 failed"  or "3 passed"
        m = re.search(r"(\d+) passed", output)
        if m:
            passed = int(m.group(1))
        m = re.search(r"(\d+) failed", output)
        if m:
            failed = int(m.group(1))

    elif language == "javascript":
        # Jest: "Tests: 2 failed, 5 passed, 7 total"
        m = re.search(r"Tests:\s+(?:(\d+) failed,\s*)?(\d+) passed", output)
        if m:
            failed = int(m.group(1) or 0)
            passed = int(m.group(2))

    elif language == "java":
        # Maven: "Tests run: 1, Failures: 0, Errors: 0, Skipped: 0"
        m = re.search(r"Tests run:\s*(\d+),\s*Failures:\s*(\d+),\s*Errors:\s*(\d+)", output)
        if m:
            total   = int(m.group(1))
            failed  = int(m.group(2)) + int(m.group(3))
            passed  = total - failed
        else:
            # Fallback for simpler output
            m_pass = re.search(r"(\d+) passed", output)
            m_fail = re.search(r"(\d+) failed", output)
            if m_pass: passed = int(m_pass.group(1))
            if m_fail: failed = int(m_fail.group(1))

    elif language == "go":
        m = re.search(r"(\d+) passed", output)
        if m:
            passed = int(m.group(1))
        if "FAIL" in output:
            failed = output.count("--- FAIL:")

    return passed, failed


async def _log(db, task_id, node, level, message, log_metadata=None):
    db.add(TaskLog(task_id=task_id, level=level, node=node,
                   message=message, log_metadata=log_metadata))
    await db.commit()
    await ws_manager.send_log(str(task_id), level.value, node, message, log_metadata)
