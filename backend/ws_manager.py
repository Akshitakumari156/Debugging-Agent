import asyncio
import json
import logging
from collections import defaultdict
from typing import Optional

import redis.asyncio as aioredis
from fastapi import WebSocket

from backend.config import settings

logger = logging.getLogger(__name__)

REDIS_CHANNEL = "autodebug:ws_events"


class WebSocketManager:
    """
    Thread-safe manager for WebSocket connections grouped by task_id.
    
    Supports multi-container synchronization via Redis Pub/Sub.
    """

    def __init__(self):
        # task_id → set of WebSocket connections
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)
        self._redis: Optional[aioredis.Redis] = None
        self._listener_task: Optional[asyncio.Task] = None

    async def _get_redis(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        return self._redis

    async def connect(self, websocket: WebSocket, task_id: str) -> None:
        """Accept a new WebSocket connection and register it."""
        await websocket.accept()
        self._connections[task_id].add(websocket)
        
        # Start the listener loop if this is the first connection in this process
        if self._listener_task is None:
            self._listener_task = asyncio.create_task(self._listen_to_redis())
            
        logger.info(f"[WS] Client connected to task {task_id} "
                    f"(total nodes watching: {len(self._connections)})")

    def disconnect(self, websocket: WebSocket, task_id: str) -> None:
        """Remove a disconnected WebSocket."""
        self._connections[task_id].discard(websocket)
        if not self._connections[task_id]:
            del self._connections[task_id]
        logger.info(f"[WS] Client disconnected from task {task_id}")

    async def broadcast(self, task_id: str, data: dict, local_only: bool = False) -> None:
        """
        Send a JSON message to all clients watching a task.
        
        By default, it also publishes to Redis so other containers (backend/worker)
        can also broadcast to their connected clients.
        """
        message_data = {**data, "task_id": task_id}
        message_json = json.dumps(message_data)

        # 1. Local Broadcast
        if task_id in self._connections:
            dead_sockets = set()
            for ws in self._connections[task_id]:
                try:
                    await ws.send_text(message_json)
                except Exception:
                    dead_sockets.add(ws)
            
            for ws in dead_sockets:
                self._connections[task_id].discard(ws)

        # 2. Redis Broadcast (sync across containers)
        if not local_only:
            try:
                redis = await self._get_redis()
                await redis.publish(REDIS_CHANNEL, message_json)
            except Exception as e:
                logger.error(f"[WS] Redis publish failed: {e}")

    async def send_log(self, task_id: str, level: str, node: str, message: str, log_metadata: dict = None) -> None:
        """Convenience method to broadcast a log entry."""
        from datetime import datetime
        await self.broadcast(task_id, {
            "event":        "log",
            "level":        level,
            "node":         node,
            "message":      message,
            "log_metadata": log_metadata,
            "created_at":   datetime.utcnow().isoformat() + "Z",
        })

    async def _listen_to_redis(self):
        """Infinite loop listening for events from other containers."""
        logger.info("[WS] Starting Redis event listener...")
        redis = await self._get_redis()
        pubsub = redis.pubsub()
        await pubsub.subscribe(REDIS_CHANNEL)
        
        try:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    data = json.loads(message["data"])
                    task_id = data.pop("task_id", None)
                    if task_id and task_id in self._connections:
                        # Broadcast locally only to avoid loops
                        await self.broadcast(task_id, data, local_only=True)
        except Exception as e:
            logger.error(f"[WS] Redis listener crashed: {e}")
            self._listener_task = None
        finally:
            await pubsub.unsubscribe(REDIS_CHANNEL)

    @property
    def active_tasks(self) -> list[str]:
        """Return task IDs with active connections."""
        return list(self._connections.keys())


# Singleton instance
ws_manager = WebSocketManager()
