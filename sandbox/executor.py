"""
sandbox/executor.py — Command Execution Inside Sandbox Containers

Provides a clean async interface for running shell commands
inside a running Docker container via the Docker SDK exec API.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

import docker
from docker.errors import APIError

logger = logging.getLogger(__name__)


@dataclass
class ExecResult:
    exit_code: int
    output: str


class SandboxExecutor:
    """
    Executes shell commands inside an existing Docker container.
    
    Features:
    - Async (runs Docker API calls in thread pool to avoid blocking)
    - Per-command timeout enforcement
    - Combined stdout+stderr capture
    - Clean error propagation
    """

    def __init__(self, container_id: str):
        self.container_id = container_id
        self._client      = docker.from_env()

    async def run(
        self,
        command: str,
        timeout: int = 60,
        workdir: Optional[str] = None,
        env: Optional[dict] = None,
    ) -> ExecResult:
        """
        Run a shell command inside the container.
        
        Args:
            command:  Shell command string (executed via /bin/sh -c)
            timeout:  Max seconds before the command is killed
            workdir:  Working directory inside container (overrides default)
            env:      Additional environment variables
        
        Returns:
            ExecResult with exit_code and combined output string
        
        Raises:
            TimeoutError: if command exceeds timeout
            RuntimeError: on Docker API errors
        """
        loop = asyncio.get_event_loop()

        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    self._exec_sync,
                    command,
                    workdir,
                    env or {},
                ),
                timeout=float(timeout),
            )
            return result

        except asyncio.TimeoutError:
            logger.warning(f"[Executor] Command timed out after {timeout}s: {command[:100]}")
            raise TimeoutError(
                f"Command exceeded {timeout}s timeout: {command[:80]}"
            )

    def _exec_sync(
        self,
        command: str,
        workdir: Optional[str],
        env: dict,
    ) -> ExecResult:
        """Synchronous exec — called from thread pool."""
        try:
            container = self._client.containers.get(self.container_id)

            kwargs = {
                "cmd":          ["/bin/sh", "-c", command],
                "stdout":       True,
                "stderr":       True,
                "demux":        False,   # Combined stdout+stderr
            }
            if workdir:
                kwargs["workdir"] = workdir
            if env:
                kwargs["environment"] = env

            # exec_run returns (exit_code, output_bytes)
            exit_code, output_bytes = container.exec_run(**kwargs)
            output = (output_bytes or b"").decode("utf-8", errors="replace")

            logger.debug(
                f"[Executor] exit={exit_code} cmd={command[:60]!r} "
                f"out={output[:100]!r}"
            )
            return ExecResult(exit_code=exit_code, output=output)

        except APIError as e:
            raise RuntimeError(f"Docker API error during exec: {e}") from e

    async def write_file(self, path: str, content: str) -> None:
        """
        Write a file into the container at the given absolute path.
        Uses base64 to safely handle special characters.
        """
        import base64
        encoded = base64.b64encode(content.encode()).decode()
        cmd = (
            f"python3 -c \""
            f"import base64, os; "
            f"p='{path}'; "
            f"os.makedirs(os.path.dirname(p), exist_ok=True); "
            f"open(p, 'w').write(base64.b64decode('{encoded}').decode())"
            f"\""
        )
        result = await self.run(cmd)
        if result.exit_code != 0:
            raise RuntimeError(f"Failed to write {path}: {result.output}")

    async def read_file(self, path: str) -> str:
        """Read a file from the container."""
        result = await self.run(f"cat {path}")
        if result.exit_code != 0:
            raise FileNotFoundError(f"Cannot read {path}: {result.output}")
        return result.output
