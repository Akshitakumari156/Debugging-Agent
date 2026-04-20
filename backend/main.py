"""
backend/main.py — FastAPI Application Entrypoint

Starts the web server with:
  - API routes (/run-task, /task-status, /logs)
  - WebSocket endpoint for real-time log streaming
  - CORS configuration
  - Startup/shutdown lifecycle hooks
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.config import settings
from backend.routers import tasks, logs
from backend.ws_manager import ws_manager
from db.database import engine
from db.models import Base

# ---------------------------------------------------------------------------
# Logging configuration
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan — startup / shutdown
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Runs on startup: create DB tables if they don't exist.
    Runs on shutdown: close DB connections.
    """
    logger.info("AutoDebug backend starting up...")
    
    # Create tables (idempotent — won't drop existing data)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    logger.info("Database tables ready.")
    
    yield  # Application runs here
    
    logger.info("AutoDebug backend shutting down...")
    await engine.dispose()


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

app = FastAPI(
    title="AutoDebug API",
    description="Autonomous GitHub Issue Resolution Agent",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(tasks.router, prefix="/api", tags=["tasks"])
app.include_router(logs.router,  prefix="/api", tags=["logs"])

# ---------------------------------------------------------------------------
# WebSocket — Real-time log streaming
# ---------------------------------------------------------------------------

from fastapi import WebSocket, WebSocketDisconnect

@app.websocket("/ws/{task_id}")
async def websocket_endpoint(websocket: WebSocket, task_id: str):
    """
    WebSocket endpoint for real-time log streaming.
    
    The frontend connects here to receive live agent updates
    without polling the REST API.
    """
    await ws_manager.connect(websocket, task_id)
    try:
        while True:
            # Keep connection alive — we send; client just listens
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, task_id)
    except Exception as e:
        logger.error(f"WebSocket error for task {task_id}: {e}")
        ws_manager.disconnect(websocket, task_id)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health", tags=["system"])
async def health_check():
    """Simple liveness probe for load balancers / Docker healthchecks."""
    return {"status": "healthy", "version": "1.0.0"}
