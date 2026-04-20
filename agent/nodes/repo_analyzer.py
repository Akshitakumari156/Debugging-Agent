"""
agent/nodes/repo_analyzer.py — Repo Analyzer Node

Responsibilities:
  1. Spawn a Docker sandbox container
  2. Clone the repository inside it
  3. Detect language + test framework
  4. Map file structure
  5. Install dependencies
"""

from __future__ import annotations

import logging
import uuid
from pathlib import PurePosixPath

from agent.state import AgentState
from backend.config import settings
from db.database import db_session
from db.models import Task, TaskLog, LogLevel, TaskStatus
from sandbox.manager import SandboxManager
from sandbox.executor import SandboxExecutor
from backend.ws_manager import ws_manager
from gh_integration.pr_manager import PRManager

logger = logging.getLogger(__name__)

# Language detection heuristics: marker files → (language, install_cmd, test_cmd)
LANGUAGE_MARKERS = {
    "requirements.txt":  ("python",     "pip install -r requirements.txt -q", "export PYTHONPATH=$PYTHONPATH:. && pytest -x -q 2>&1"),
    "pyproject.toml":    ("python",     "pip install -e . -q",                "export PYTHONPATH=$PYTHONPATH:. && pytest -x -q 2>&1"),
    "setup.py":          ("python",     "pip install -e . -q",                "export PYTHONPATH=$PYTHONPATH:. && pytest -x -q 2>&1"),
    "package.json":      ("javascript", "npm install --silent",               "npm test -- --watchAll=false"),
    "pom.xml":           ("java",       "mvn install -q -DskipTests",         "mvn test -q"),
    "build.gradle":      ("java",       "gradle build -q",                    "gradle test -q"),
    "go.mod":            ("go",         "go mod download",                    "go test ./... -v"),
    "Cargo.toml":        ("rust",       "cargo build -q",                     "cargo test -q"),
    "Gemfile":           ("ruby",       "bundle install -q",                  "bundle exec rspec"),
}


async def repo_analyzer_node(state: AgentState) -> AgentState:
    """
    Repo Analyzer Node.

    Creates an isolated Docker container and clones the target repository.
    Detects the language and installs dependencies so the Bug Reproducer can
    immediately start running tests.
    """
    task_id = state["task_id"]
    logger.info(f"[RepoAnalyzer] Starting for task {task_id}")

    async with db_session() as db:
        task_uuid = uuid.UUID(task_id)
        task = await db.get(Task, task_uuid)
        task.status = TaskStatus.CLONING
        task.current_node = "repo_analyzer"
        await db.commit()

        try:
            repo_url = state["repo_url"]
            pr_manager = PRManager(settings.GITHUB_TOKEN)
            
            # --- FORK LOGIC ---
            await _log(db, task_id, "repo_analyzer", LogLevel.INFO, f"Checking permissions for {repo_url}...")
            fork_info = await pr_manager.fork_repo(repo_url)
            
            is_forked = fork_info["is_forked"]
            clone_url = fork_info["clone_url"]
            if is_forked:
                await _log(db, task_id, "repo_analyzer", LogLevel.INFO, 
                           f"Using forked repo for development: {fork_info['repo_full_name']}")
            else:
                await _log(db, task_id, "repo_analyzer", LogLevel.INFO, 
                           "Direct write access confirmed.")

            # -----------------------------------------------------------------
            # 1. Create sandbox container
            # -----------------------------------------------------------------
            manager = SandboxManager()
            container_id = await manager.create(task_id)
            executor = SandboxExecutor(container_id)

            await _log(db, task_id, "repo_analyzer", LogLevel.INFO,
                       f"Sandbox container started: {container_id[:12]}")

            # -----------------------------------------------------------------
            # 2. Clone repository
            # -----------------------------------------------------------------
            workspace = f"/workspace/{task_id}"

            clone_result = await executor.run(
                f"git clone --depth 1 {clone_url} {workspace} 2>&1"
            )
            if clone_result.exit_code != 0:
                raise RuntimeError(f"git clone failed:\n{clone_result.output}")

            await _log(db, task_id, "repo_analyzer", LogLevel.INFO,
                       f"Cloned {clone_url} → {workspace}")

            # -----------------------------------------------------------------
            # 3. Detect language
            # -----------------------------------------------------------------
            ls_result = await executor.run(f"ls {workspace}")
            files_in_root = ls_result.output.split()

            language, install_cmd, test_cmd = "unknown", "echo 'no install'", "echo 'no tests'"
            for marker, (lang, install, test) in LANGUAGE_MARKERS.items():
                if marker in files_in_root:
                    language, install_cmd, test_cmd = lang, install, test
                    break

            await _log(db, task_id, "repo_analyzer", LogLevel.INFO,
                       f"Detected language: {language}")

            # -----------------------------------------------------------------
            # 4. Map file structure (limit depth to avoid huge outputs)
            # -----------------------------------------------------------------
            tree_result = await executor.run(
                f"find {workspace} -not -path '*/.git/*' -not -path '*/node_modules/*' "
                f"-not -path '*/__pycache__/*' | head -200"
            )
            repo_structure = {"files": tree_result.output.strip().split("\n")}

            # -----------------------------------------------------------------
            # 5. Install dependencies
            # -----------------------------------------------------------------
            await _log(db, task_id, "repo_analyzer", LogLevel.INFO,
                       f"Installing dependencies: {install_cmd}")

            install_result = await executor.run(
                f"cd {workspace} && {install_cmd}", timeout=settings.SANDBOX_TIMEOUT
            )

            if install_result.exit_code != 0:
                await _log(db, task_id, "repo_analyzer", LogLevel.WARNING,
                           f"Dependency install warnings:\n{install_result.output[:1000]}")

            # -----------------------------------------------------------------
            # 6. Persist to DB
            # -----------------------------------------------------------------
            task.container_id   = container_id
            task.workspace_path = workspace
            await db.commit()

            return {
                **state,
                "container_id":   container_id,
                "workspace_path": workspace,
                "language":       language,
                "install_command": install_cmd,
                "test_command":   test_cmd,
                "repo_structure": repo_structure,
                "is_forked":      is_forked,
                "clone_url":      clone_url,
                "fork_owner":     fork_info.get("owner_login"),
            }

        except Exception as exc:
            logger.exception(f"[RepoAnalyzer] Failed: {exc}")
            await _log(db, task_id, "repo_analyzer", LogLevel.ERROR, str(exc))
            # Try to preserve container_id in state if it was already created 
            # to avoid KeyError in future nodes
            error_state = {
                **state, 
                "error": f"RepoAnalyzer failed: {exc}"
            }
            if 'container_id' in locals():
                error_state["container_id"] = container_id
            if 'clone_url' in locals():
                error_state["clone_url"] = clone_url
            if 'is_forked' in locals():
                error_state["is_forked"] = is_forked
            if 'fork_info' in locals():
                error_state["fork_owner"] = fork_info.get("owner_login")
            return error_state


async def _log(db, task_id, node, level, message, log_metadata=None):
    db.add(TaskLog(task_id=task_id, level=level, node=node,
                   message=message, log_metadata=log_metadata))
    await db.commit()
    await ws_manager.send_log(str(task_id), level.value, node, message, log_metadata)
