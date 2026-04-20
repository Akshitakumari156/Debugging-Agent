"""
agent/nodes/fix_generator.py — Fix Generator Node

Responsibilities:
  1. Read all relevant source files
  2. Use LLM (with chain-of-thought) to:
     a. Identify the root cause
     b. Generate a minimal, targeted fix
  3. Apply the patch to the sandbox container
"""

from __future__ import annotations

import json
import logging
import re
import uuid

from langchain_core.messages import HumanMessage, SystemMessage
from agent.llm import get_llm as _get_llm

from agent.state import AgentState
from backend.config import settings
from db.database import db_session
from db.models import Task, TaskLog, LogLevel, TaskStatus, FixAttempt, FixStatus
from sandbox.executor import SandboxExecutor
from backend.ws_manager import ws_manager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

FIX_SYSTEM_PROMPT = """You are an expert software engineer performing code review and bug fixing.

You will be given:
1. A bug report (issue title + description)
2. The error trace from a failing test
3. The source code of relevant files

Your response MUST be valid JSON with this schema:
{
  "root_cause": "Precise explanation of WHY the bug occurs",
  "fix_plan": "Step-by-step plan for the fix",
  "files": [
    {
      "path": "relative/path/to/file.py",
      "content": "COMPLETE new content of the file (not a diff)"
    }
  ],
  "reasoning": "Chain-of-thought explanation of the fix"
}

Rules:
- Only modify files that are NECESSARY to fix the bug
- Keep changes MINIMAL — don't refactor unrelated code
- Follow the existing code style exactly
- Do not add comments unless the original code had them
- "content" must be the COMPLETE file content, ready to write directly
"""

FIX_HUMAN_TEMPLATE = """Issue: {issue_title}

Description: {issue_body}

Error Trace:
{error_trace}

Reproduction Output:
{reproduction_output}

Source Files:
{source_files}

{reflection_notes}

Generate a fix. Iteration {iteration} of {max_iterations}."""


async def fix_generator_node(state: AgentState) -> AgentState:
    """
    Fix Generator Node.
    
    Reads relevant source files, asks LLM to analyze root cause
    and generate a complete fix, then applies it to the sandbox.
    """
    task_id   = state["task_id"]
    iteration = state.get("iteration", 0)
    logger.info(f"[FixGenerator] Starting iteration {iteration} for task {task_id}")

    async with db_session() as db:
        task_uuid = uuid.UUID(task_id)
        task = await db.get(Task, task_uuid)
        task.status = TaskStatus.FIXING
        task.current_node = "fix_generator"
        task.iteration = iteration
        await db.commit()

        try:
            executor  = SandboxExecutor(state["container_id"])
            workspace = state["workspace_path"]

            # -----------------------------------------------------------------
            # 1. Read relevant source files
            # -----------------------------------------------------------------
            source_files = await _read_source_files(executor, workspace, state)

            # -----------------------------------------------------------------
            # 2. Call LLM for root cause + fix
            # -----------------------------------------------------------------
            reflection_section = ""
            if state.get("reflection_notes"):
                reflection_section = f"\nPrevious fix attempt failed:\n{state['reflection_notes']}\n"

            llm = _get_llm()
            messages = [
                SystemMessage(content=FIX_SYSTEM_PROMPT),
                HumanMessage(content=FIX_HUMAN_TEMPLATE.format(
                    issue_title=state.get("issue_title", ""),
                    issue_body=state.get("issue_body", "")[:2000],
                    error_trace=state.get("error_trace", "")[:2000],
                    reproduction_output=state.get("reproduction_output", "")[:1500],
                    source_files=source_files[:6000],
                    reflection_notes=reflection_section,
                    iteration=iteration + 1,
                    max_iterations=settings.MAX_FIX_ITERATIONS,
                )),
            ]

            response = await llm.ainvoke(messages)
            raw = response.content.strip()

            # Robust JSON extraction and cleaning
            json_match = re.search(r"(\{.*\})", raw, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
                try:
                    # Try direct load first
                    fix_data = json.loads(json_str, strict=False)
                except json.JSONDecodeError:
                    try:
                        # Attempt to fix "Invalid control character" (literal newlines/tabs)
                        # This regex finds text inside double quotes and escapes literal newlines
                        def fix_control_chars(m):
                            return m.group(0).replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
                        fixed_json = re.sub(r'"(.*?)"', fix_control_chars, json_str, flags=re.DOTALL)
                        fix_data = json.loads(fixed_json, strict=False)
                    except json.JSONDecodeError as e:
                        logger.error(f"[FixGenerator] JSON parse error: {e}. Raw content: {raw}")
                        raise RuntimeError(f"Failed to parse LLM response as JSON: {e}")
            else:
                raise RuntimeError("LLM response did not contain any JSON structure.")

            root_cause   = fix_data["root_cause"]
            fix_plan     = fix_data["fix_plan"]
            llm_reasoning = fix_data.get("reasoning", "")
            files_to_fix = fix_data["files"]

            await _log(db, task_id, "fix_generator", LogLevel.INFO,
                       f"Root cause identified: {root_cause[:300]}")

            # -----------------------------------------------------------------
            # 3. Apply changes to sandbox
            # -----------------------------------------------------------------
            files_modified = []
            full_diff      = []

            for file_spec in files_to_fix:
                rel_path  = file_spec["path"]
                new_content = file_spec["content"]
                abs_path  = f"{workspace}/{rel_path}"

                # Get old content for diff
                old_result = await executor.run(f"cat {abs_path} 2>/dev/null")
                old_content = old_result.output if old_result.exit_code == 0 else ""

                # Write new content (using Python here-doc trick to preserve special chars)
                write_cmd = (
                    f"python3 -c \""
                    f"import base64; "
                    f"content = base64.b64decode('{_b64(new_content)}').decode(); "
                    f"open('{abs_path}', 'w').write(content)"
                    f"\""
                )
                write_result = await executor.run(write_cmd)

                if write_result.exit_code == 0:
                    files_modified.append(rel_path)
                    # Generate unified diff for logging
                    diff_result = await executor.run(
                        f"diff -u - {abs_path} <<< '{_escape(old_content)}' 2>/dev/null || true"
                    )
                    full_diff.append(f"--- a/{rel_path}\n+++ b/{rel_path}")

                    await _log(db, task_id, "fix_generator", LogLevel.INFO,
                               f"Applied fix to {rel_path}")
                else:
                    await _log(db, task_id, "fix_generator", LogLevel.WARNING,
                               f"Failed to write {rel_path}: {write_result.output[:200]}")

            # -----------------------------------------------------------------
            # 4. Persist fix attempt
            # -----------------------------------------------------------------
            attempt = FixAttempt(
                task_id=task_id,
                iteration=iteration,
                diff_patch="\n".join(full_diff),
                files_modified=files_modified,
                llm_reasoning=llm_reasoning,
                status=FixStatus.GENERATED,
            )
            db.add(attempt)
            task.root_cause  = root_cause
            # Ensure fix_plan is a string for the DB (expected str, got list error)
            task.fix_summary = "\n".join(fix_plan) if isinstance(fix_plan, list) else str(fix_plan)
            await db.commit()

            return {
                **state,
                "root_cause":     root_cause,
                "fix_plan":       fix_plan,
                "llm_reasoning":  llm_reasoning,
                "diff_patch":     "\n".join(full_diff),
                "files_modified": files_modified,
                "relevant_code":  {f["path"]: f["content"] for f in files_to_fix},
            }

        except Exception as exc:
            logger.exception(f"[FixGenerator] Failed: {exc}")
            await _log(db, task_id, "fix_generator", LogLevel.ERROR, str(exc))
            return {**state, "error": f"FixGenerator failed: {exc}"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _read_source_files(executor, workspace: str, state: AgentState) -> str:
    """Read suspected + any files mentioned in the error trace."""
    file_paths = list(state.get("suspected_files", []))

    # Also parse error trace for file references
    error_trace = state.get("error_trace", "")
    trace_files = re.findall(r'File "([^"]+)"', error_trace)
    for tf in trace_files:
        rel = tf.replace(workspace + "/", "")
        if rel not in file_paths and not rel.startswith("/"):
            file_paths.append(rel)

    parts = []
    for fpath in file_paths[:8]:  # cap at 8 files to manage context window
        result = await executor.run(f"cat {workspace}/{fpath} 2>/dev/null")
        if result.exit_code == 0 and result.output.strip():
            parts.append(f"### {fpath}\n```\n{result.output}\n```")

    return "\n\n".join(parts)


def _b64(text: str) -> str:
    import base64
    return base64.b64encode(text.encode()).decode()


def _escape(text: str) -> str:
    return text.replace("'", "'\\''")


# _get_llm is imported from agent.llm


async def _log(db, task_id, node, level, message, log_metadata=None):
    db.add(TaskLog(task_id=task_id, level=level, node=node,
                   message=message, log_metadata=log_metadata))
    await db.commit()
    await ws_manager.send_log(str(task_id), level.value, node, message, log_metadata)
