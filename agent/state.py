"""
agent/state.py — LangGraph shared state schema

This TypedDict is passed between every node in the graph.
All fields are optional so nodes can read only what they need.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional, TypedDict


class AgentState(TypedDict, total=False):
    # -------------------------------------------------------------------------
    # Inputs (set by orchestrator before graph runs)
    # -------------------------------------------------------------------------
    task_id: str                       # UUID of the Task record in DB
    repo_url: str                      # e.g. "https://github.com/owner/repo"
    issue_id: Optional[int]            # GitHub issue number
    issue_title: str
    issue_body: str                    # Raw issue text from GitHub

    # -------------------------------------------------------------------------
    # Planner outputs
    # -------------------------------------------------------------------------
    plan: dict                         # Structured plan from LLM
    suspected_files: list[str]         # File paths likely containing the bug
    reproduction_steps: list[str]      # Human-readable steps to reproduce
    required_tools: list[str]          # Tools the agent needs

    # -------------------------------------------------------------------------
    # Sandbox / Repo setup
    # -------------------------------------------------------------------------
    container_id: str                  # Running Docker container ID
    workspace_path: str                # Path inside container where repo lives
    language: str                      # Detected: python | javascript | java | go
    test_command: str                  # e.g. "pytest -x" or "npm test"
    install_command: str               # e.g. "pip install -e ." or "npm install"
    repo_structure: dict               # File tree of the repository

    # -------------------------------------------------------------------------
    # Bug reproduction
    # -------------------------------------------------------------------------
    reproduction_output: str           # stdout/stderr from running tests
    reproduction_passed: bool          # True if we successfully reproduced the bug
    failing_tests: list[str]           # Names of failing tests/specs
    error_trace: str                   # Extracted stack trace

    # -------------------------------------------------------------------------
    # Root cause analysis
    # -------------------------------------------------------------------------
    relevant_code: dict[str, str]      # {filepath: content} of relevant files
    root_cause: str                    # LLM-generated root cause explanation
    fix_plan: str                      # Step-by-step fix plan

    # -------------------------------------------------------------------------
    # Fix generation
    # -------------------------------------------------------------------------
    iteration: int                     # Current fix attempt (0-indexed)
    diff_patch: str                    # Unified diff of proposed changes
    files_modified: list[str]         # Which files were changed
    llm_reasoning: str                 # Chain-of-thought from fix LLM

    # -------------------------------------------------------------------------
    # Test validation
    # -------------------------------------------------------------------------
    test_output: str                   # stdout/stderr from test run
    tests_passed: int
    tests_failed: int
    validation_success: bool           # True = bug fixed, no regressions

    # -------------------------------------------------------------------------
    # Reflection (retry analysis)
    # -------------------------------------------------------------------------
    reflection_notes: str              # Analysis of why the fix failed

    # -------------------------------------------------------------------------
    # PR creation
    # -------------------------------------------------------------------------
    branch_name: str
    commit_sha: str
    pr_url: str
    pr_number: int
    pr_description: str

    # -------------------------------------------------------------------------
    # Control flow
    # -------------------------------------------------------------------------
    error: Optional[str]               # Non-None = something broke
    should_retry: bool                 # Reflector sets this
    is_done: bool                      # Terminal state reached
