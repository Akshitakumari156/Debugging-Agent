"""
agent/nodes/bug_reproducer.py — Bug Reproducer Node

Responsibilities:
  1. Run existing test suite — capture any pre-existing failures
  2. If no failures found, generate a minimal failing test using LLM
  3. Return the error trace + failing test names
"""

from __future__ import annotations

import logging
import re
import uuid

from langchain_core.messages import HumanMessage, SystemMessage
from agent.llm import get_llm as _get_llm

from agent.state import AgentState
from backend.config import settings
from db.database import db_session
from db.models import Task, TaskLog, LogLevel, TaskStatus
from sandbox.executor import SandboxExecutor
from backend.ws_manager import ws_manager

logger = logging.getLogger(__name__)

REPRODUCE_SYSTEM_PROMPT = """You are a senior software engineer.
Your task is to write a minimal test that demonstrates the bug and serves as a 'specification' for the correct behavior.

The test should:
1. Be self-contained and runnable.
2. ASSERT THE CORRECT BEHAVIOR (what the code SHOULD do if it weren't buggy).
3. Fail on the current buggy code because the assertion for correct behavior is not met.
4. Pass 100% once the bug is fixed.
5. Use the existing test framework for this project.

DATA REQUIREMENTS:
- If testing Machine Learning or Statistics code (e.g., using scikit-learn, pandas, numpy), you MUST provide at least 20-50 rows of dummy data. 
- Small datasets (< 10 rows) cause metrics like R^2 to return 'NaN' and will fail the test.

CRITICAL: Do not use 'assertRaises' to check for the bug. Instead, assert the expected successful result.
Respond ONLY with the test code (no explanation, no markdown fences).
"""

REPRODUCE_HUMAN_TEMPLATE = """Issue: {issue_title}

Description: {issue_body}

Existing test output:
{test_output}

Relevant files:
{relevant_code}

Write a minimal failing test that reproduces this bug.
Save it in the test directory using the project's conventions.
"""


async def bug_reproducer_node(state: AgentState) -> AgentState:
    """
    Bug Reproducer Node.
    
    Runs the test suite to see if it already captures the failure.
    If not, generates a targeted failing test using the LLM.
    """
    task_id = state["task_id"]
    logger.info(f"[BugReproducer] Starting for task {task_id}")

    async with db_session() as db:
        task_uuid = uuid.UUID(task_id)
        task = await db.get(Task, task_uuid)
        task.status = TaskStatus.REPRODUCING
        task.current_node = "bug_reproducer"
        await db.commit()

        try:
            executor    = SandboxExecutor(state["container_id"])
            workspace   = state["workspace_path"]
            test_cmd    = state["test_command"]

            # -----------------------------------------------------------------
            # 1. Run existing test suite
            # -----------------------------------------------------------------
            await _log(db, task_id, "bug_reproducer", LogLevel.INFO,
                       f"Running test suite: {test_cmd}")

            result = await executor.run(
                f"cd {workspace} && {test_cmd}", timeout=120
            )
            test_output = result.output

            await _log(db, task_id, "bug_reproducer", LogLevel.INFO,
                       "Test run complete", {"exit_code": result.exit_code,
                                             "output_preview": test_output[:500]})

            # -----------------------------------------------------------------
            # 2. Check if the bug is already captured
            # -----------------------------------------------------------------
            failing_tests = _extract_failing_tests(test_output, state["language"])
            error_trace   = _extract_error_trace(test_output)
            bug_already_reproduced = bool(failing_tests or error_trace)

            if bug_already_reproduced:
                await _log(db, task_id, "bug_reproducer", LogLevel.INFO,
                           f"Bug reproduced via existing tests. Failing: {failing_tests}")
                return {
                    **state,
                    "reproduction_output":  test_output,
                    "reproduction_passed":  True,
                    "failing_tests":        failing_tests,
                    "error_trace":          error_trace,
                }

            # -----------------------------------------------------------------
            # 3. No existing failure — generate a targeted test via LLM
            # -----------------------------------------------------------------
            await _log(db, task_id, "bug_reproducer", LogLevel.INFO,
                       "No existing failure found. Generating reproduction test...")

            relevant_code = await _read_suspected_files(executor, workspace, state)
            llm = _get_llm()

            messages = [
                SystemMessage(content=REPRODUCE_SYSTEM_PROMPT),
                HumanMessage(content=REPRODUCE_HUMAN_TEMPLATE.format(
                    issue_title=state.get("issue_title", ""),
                    issue_body=state.get("issue_body", "")[:2000],
                    test_output=test_output[:1500],
                    relevant_code=relevant_code[:3000],
                )),
            ]

            generated_raw = (await llm.ainvoke(messages)).content.strip()
            
            # Robustly strip markdown code fences if present
            generated_test = re.sub(r"^```\w*\n", "", generated_raw)
            generated_test = re.sub(r"\n```$", "", generated_test)
            generated_test = generated_test.strip()

            # Write test file into the container
            test_filename = _choose_test_filename(state["language"])
            
            # Ensure parent directory exists
            await executor.run(f"mkdir -p {workspace}/{test_filename.rsplit('/', 1)[0]}")
            
            write_result = await executor.run(
                f"cat > {workspace}/{test_filename} << 'AUTODEBUG_EOF'\n"
                f"{generated_test}\n"
                f"AUTODEBUG_EOF"
            )

            if write_result.exit_code != 0:
                await _log(db, task_id, "bug_reproducer", LogLevel.WARNING,
                           f"Failed to write reproduction test: {write_result.output}")
            else:
                await _log(db, task_id, "bug_reproducer", LogLevel.INFO,
                           f"Reproduction test created: {test_filename}")

            # Run again with the new test
            result2 = await executor.run(
                f"cd {workspace} && {test_cmd}", timeout=120
            )
            test_output2 = result2.output
            failing_tests2 = _extract_failing_tests(test_output2, state["language"])
            error_trace2   = _extract_error_trace(test_output2)

            await _log(db, task_id, "bug_reproducer", LogLevel.INFO,
                       f"Generated test result: exit_code={result2.exit_code}")

            return {
                **state,
                "reproduction_output": test_output2,
                "reproduction_passed": bool(failing_tests2 or result2.exit_code != 0),
                "failing_tests":       failing_tests2,
                "error_trace":         error_trace2,
            }

        except Exception as exc:
            logger.exception(f"[BugReproducer] Failed: {exc}")
            await _log(db, task_id, "bug_reproducer", LogLevel.ERROR, str(exc))
            return {**state, "error": f"BugReproducer failed: {exc}"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_failing_tests(output: str, language: str) -> list[str]:
    """Extract failing test names from test runner output."""
    failing = []
    if language == "python":
        # pytest: FAILED tests/test_foo.py::test_bar
        failing = re.findall(r"FAILED\s+([\w/:.]+)", output)
    elif language == "javascript":
        failing = re.findall(r"✕\s+(.+)", output)
    elif language == "java":
        failing = re.findall(r"Tests run:.*?Failures: ([^,]+)", output)
    return failing


def _extract_error_trace(output: str) -> str:
    """Extract the most relevant portion of a stack trace."""
    # Look for common error patterns
    patterns = [
        r"(Traceback \(most recent call last\):.*?)(?=\n\n|\Z)",
        r"(Error:.*?\n(?:\s+at .+\n)+)",
        r"(Exception in thread.*?\n(?:\s+at .+\n)+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, output, re.DOTALL)
        if match:
            return match.group(1)
    return ""


def _choose_test_filename(language: str) -> str:
    mapping = {
        "python":     "tests/test_autodebug_reproduction.py",
        "javascript": "__tests__/autodebug_reproduction.test.js",
        "java":       "src/test/java/AutoDebugReproductionTest.java",
        "go":         "autodebug_reproduction_test.go",
        "ruby":       "spec/autodebug_reproduction_spec.rb",
    }
    return mapping.get(language, "test_reproduction.py")


async def _read_suspected_files(executor, workspace: str, state: AgentState) -> str:
    """Read contents of suspected files for LLM context."""
    parts = []
    for fpath in state.get("suspected_files", [])[:5]:
        result = await executor.run(f"cat {workspace}/{fpath} 2>/dev/null | head -100")
        if result.exit_code == 0:
            parts.append(f"### {fpath}\n{result.output}")
    return "\n\n".join(parts)


# _get_llm is imported from agent.llm


async def _log(db, task_id, node, level, message, log_metadata=None):
    db.add(TaskLog(task_id=task_id, level=level, node=node,
                   message=message, log_metadata=log_metadata))
    await db.commit()
    await ws_manager.send_log(str(task_id), level.value, node, message, log_metadata)
