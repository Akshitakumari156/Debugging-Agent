"""
agent/nodes/planner.py — Planner Node

Responsibilities:
  1. Fetch the GitHub issue details (title + body)
  2. Use GPT-4o to create a structured debugging plan
  3. Identify suspected files and reproduction steps
"""

from __future__ import annotations

import json
import logging
import uuid

from langchain_core.messages import HumanMessage, SystemMessage
from agent.llm import get_llm as _get_llm

from agent.state import AgentState
from backend.config import settings
from gh_integration.client import GitHubClient
from db.database import db_session
from db.models import Task, TaskLog, LogLevel, TaskStatus
from backend.ws_manager import ws_manager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

PLANNER_SYSTEM_PROMPT = """You are an expert software engineer specializing in debugging.
Your job is to analyze a GitHub issue and create a precise debugging plan.

You MUST respond with valid JSON matching this exact schema:
{
  "plan_summary": "1-2 sentence summary of the debugging approach",
  "suspected_files": ["path/to/file1.py", "path/to/file2.py"],
  "reproduction_steps": [
    "Step 1: ...",
    "Step 2: ..."
  ],
  "root_cause_hypothesis": "Initial hypothesis about what is wrong",
  "required_tools": ["terminal", "file_editor", "test_runner", "git"]
}

Guidelines:
- suspected_files: List most likely files containing the bug (max 10)
- reproduction_steps: Concrete, runnable steps
- Be specific — avoid vague descriptions
"""

PLANNER_HUMAN_TEMPLATE = """Repository: {repo_url}

Issue #{issue_id}: {issue_title}

Issue Description:
{issue_body}

Create a detailed debugging plan."""


# ---------------------------------------------------------------------------
# Node function
# ---------------------------------------------------------------------------

async def planner_node(state: AgentState) -> AgentState:
    """
    Planner Node — entrypoint of the agent graph.
    
    Fetches issue details from GitHub, then uses an LLM to build
    a structured plan before any code is executed.
    """
    task_id = state["task_id"]
    logger.info(f"[Planner] Starting for task {task_id}")

    async with db_session() as db:
        # Update task status
        task_uuid = uuid.UUID(task_id)
        task = await db.get(Task, task_uuid)
        task.status = TaskStatus.PLANNING
        task.current_node = "planner"
        await db.commit()

        try:
            # -----------------------------------------------------------------
            # 1. Fetch issue from GitHub (if not already provided)
            # -----------------------------------------------------------------
            gh = GitHubClient(settings.GITHUB_TOKEN)
            issue_title = state.get("issue_title", "")
            issue_body  = state.get("issue_body", "")

            if state.get("issue_id") and (not issue_title or not issue_body):
                issue = await gh.get_issue(state["repo_url"], state["issue_id"])
                issue_title = issue.title
                issue_body  = issue.body or ""

            # -----------------------------------------------------------------
            # 2. Build LLM and generate plan
            # -----------------------------------------------------------------
            llm = _get_llm()

            messages = [
                SystemMessage(content=PLANNER_SYSTEM_PROMPT),
                HumanMessage(content=PLANNER_HUMAN_TEMPLATE.format(
                    repo_url=state["repo_url"],
                    issue_id=state.get("issue_id", "N/A"),
                    issue_title=issue_title,
                    issue_body=issue_body[:4000],   # cap context window usage
                )),
            ]

            response = await llm.ainvoke(messages)
            plan_raw = response.content

            # Parse JSON — strip markdown fences if present
            plan_raw = plan_raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
            plan = json.loads(plan_raw)

            await _log(db, task_id, "planner", LogLevel.INFO,
                       f"Plan created: {plan['plan_summary']}", {"plan": plan})

            # -----------------------------------------------------------------
            # 3. Persist issue details to Task record
            # -----------------------------------------------------------------
            task.issue_title = issue_title
            task.issue_body  = issue_body
            await db.commit()

            logger.info(f"[Planner] Done. Suspected files: {plan['suspected_files']}")

            return {
                **state,
                "issue_title":         issue_title,
                "issue_body":          issue_body,
                "plan":                plan,
                "suspected_files":     plan["suspected_files"],
                "reproduction_steps":  plan["reproduction_steps"],
                "required_tools":      plan.get("required_tools", ["terminal"]),
            }

        except Exception as exc:
            logger.exception(f"[Planner] Failed: {exc}")
            await _log(db, task_id, "planner", LogLevel.ERROR, str(exc))
            return {**state, "error": f"Planner failed: {exc}"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# _get_llm is imported from agent.llm


async def _log(db, task_id, node, level, message, log_metadata=None):
    log = TaskLog(
        task_id=task_id,
        level=level,
        node=node,
        message=message,
        log_metadata=log_metadata,
    )
    db.add(log)
    await db.commit()
    
    # Real-time broadcast
    await ws_manager.send_log(str(task_id), level.value, node, message, log_metadata)
