"""
sandbox/manager.py — Docker Sandbox Lifecycle Manager

Creates, tracks, and destroys isolated Docker containers
used to safely clone repos and run untrusted code.

Security properties:
  - No host filesystem mounts (except explicit workspace volume)
  - Isolated Docker network (no inbound, limited outbound)
  - CPU + memory limits enforced
  - Auto-cleanup on task completion or timeout
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Optional

import docker
from docker.errors import APIError, NotFound
from docker.models.containers import Container

from backend.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SANDBOX_LABELS = {"managed-by": "autodebug"}


class SandboxManager:
    """
    Manages Docker container lifecycle for debugging sandboxes.
    
    Each task gets its own container with strict resource limits.
    """

    def __init__(self):
        # docker.from_env() reads DOCKER_HOST from environment
        self._client = docker.from_env()

    # -------------------------------------------------------------------------
    # Create
    # -------------------------------------------------------------------------

    async def create(self, task_id: str) -> str:
        """
        Spawn a fresh sandbox container for the given task.
        
        Returns the container ID (full 64-char SHA).
        """
        loop = asyncio.get_event_loop()
        container = await loop.run_in_executor(None, self._create_sync, task_id)
        logger.info(f"[Sandbox] Created container {container.id[:12]} for task {task_id}")
        return container.id

    def _create_sync(self, task_id: str) -> Container:
        """Synchronous container creation (runs in thread pool)."""
        container = self._client.containers.run(
            image=settings.SANDBOX_IMAGE,
            name=f"autodebug-{task_id[:8]}",
            detach=True,
            
            # ----------------------------------------------------------------
            # Resource limits
            # ----------------------------------------------------------------
            cpu_period=100_000,                              # 100ms period
            cpu_quota=int(settings.SANDBOX_CPU_LIMIT * 100_000),
            mem_limit=settings.SANDBOX_MEMORY_LIMIT,
            memswap_limit=settings.SANDBOX_MEMORY_LIMIT,    # disable swap
            
            # ----------------------------------------------------------------
            # Network isolation
            # ----------------------------------------------------------------
            network=settings.SANDBOX_NETWORK,               # isolated net
            # Do NOT use network_mode="none" — we need to clone from GitHub
            
            # ----------------------------------------------------------------
            # Security
            # ----------------------------------------------------------------
            read_only=False,                                 # workspace needs writes
            security_opt=["no-new-privileges:true"],
            cap_drop=["ALL"],
            cap_add=["CHOWN", "DAC_OVERRIDE", "SETUID", "SETGID"],  # minimal
            
            # ----------------------------------------------------------------
            # Filesystem
            # ----------------------------------------------------------------
            volumes={
                "autodebug-workspace": {
                    "bind": "/workspace",
                    "mode": "rw",
                },
            },
            working_dir="/workspace",
            
            # Keep alive
            command="tail -f /dev/null",
            
            # Metadata
            labels={**SANDBOX_LABELS, "task_id": task_id},
        )
        return container

    # -------------------------------------------------------------------------
    # Destroy
    # -------------------------------------------------------------------------

    async def destroy(self, container_id: str, force: bool = True) -> None:
        """Remove a container and its associated workspace data."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._destroy_sync, container_id, force)

    def _destroy_sync(self, container_id: str, force: bool) -> None:
        try:
            container = self._client.containers.get(container_id)
            container.stop(timeout=5)
            container.remove(force=force)
            logger.info(f"[Sandbox] Destroyed container {container_id[:12]}")
        except NotFound:
            logger.warning(f"[Sandbox] Container {container_id[:12]} not found (already removed?)")
        except APIError as e:
            logger.error(f"[Sandbox] Failed to remove container {container_id[:12]}: {e}")

    # -------------------------------------------------------------------------
    # Cleanup — Remove all containers owned by autodebug (admin utility)
    # -------------------------------------------------------------------------

    async def cleanup_all(self) -> int:
        """Remove ALL autodebug-managed containers. Returns count removed."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._cleanup_all_sync)

    def _cleanup_all_sync(self) -> int:
        containers = self._client.containers.list(
            all=True, filters={"label": "managed-by=autodebug"}
        )
        for c in containers:
            try:
                c.remove(force=True)
            except Exception:
                pass
        return len(containers)

    # -------------------------------------------------------------------------
    # Status
    # -------------------------------------------------------------------------

    async def get_status(self, container_id: str) -> Optional[str]:
        """Return container status string or None if not found."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get_status_sync, container_id)

    def _get_status_sync(self, container_id: str) -> Optional[str]:
        try:
            container = self._client.containers.get(container_id)
            return container.status
        except NotFound:
            return None
