"""
db/models.py — SQLAlchemy ORM models for AutoDebug

Tables:
  - tasks        : Top-level debugging task lifecycle
  - task_logs    : Append-only event log per task
  - fix_attempts : Each generation + test cycle
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Column, DateTime, Enum, ForeignKey, Integer, String, Text, Float
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TaskStatus(str, enum.Enum):
    PENDING     = "pending"
    PLANNING    = "planning"
    CLONING     = "cloning"
    REPRODUCING = "reproducing"
    ANALYZING   = "analyzing"
    FIXING      = "fixing"
    TESTING     = "testing"
    PR_CREATING = "pr_creating"
    COMPLETED   = "completed"
    FAILED      = "failed"
    CANCELLED   = "cancelled"


class LogLevel(str, enum.Enum):
    DEBUG   = "debug"
    INFO    = "info"
    WARNING = "warning"
    ERROR   = "error"


class FixStatus(str, enum.Enum):
    GENERATED = "generated"
    PASSED    = "passed"
    FAILED    = "failed"


# ---------------------------------------------------------------------------
# Task — Top-level entity
# ---------------------------------------------------------------------------

class Task(Base):
    __tablename__ = "tasks"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repo_url        = Column(String(512), nullable=False)
    issue_id        = Column(Integer, nullable=True)
    issue_title     = Column(String(512), nullable=True)
    issue_body      = Column(Text, nullable=True)

    status          = Column(Enum(TaskStatus), default=TaskStatus.PENDING, nullable=False)
    current_node    = Column(String(64), nullable=True)   # Which agent node is active
    iteration       = Column(Integer, default=0)           # Fix retry count

    # Results
    pr_url          = Column(String(512), nullable=True)
    pr_number       = Column(Integer, nullable=True)
    branch_name     = Column(String(256), nullable=True)
    root_cause      = Column(Text, nullable=True)
    fix_summary     = Column(Text, nullable=True)

    # Sandbox
    container_id    = Column(String(128), nullable=True)
    workspace_path  = Column(String(512), nullable=True)

    # Timing
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    updated_at      = Column(DateTime(timezone=True), onupdate=func.now())
    completed_at    = Column(DateTime(timezone=True), nullable=True)
    duration_secs   = Column(Float, nullable=True)

    # Error info
    error_message   = Column(Text, nullable=True)

    # Relationships
    logs            = relationship("TaskLog", back_populates="task", cascade="all, delete-orphan")
    fix_attempts    = relationship("FixAttempt", back_populates="task", cascade="all, delete-orphan")

    @property
    def task_id(self) -> str:
        return str(self.id)

    def __repr__(self) -> str:
        return f"<Task id={self.id} status={self.status} repo={self.repo_url}>"


# ---------------------------------------------------------------------------
# TaskLog — Append-only event stream per task
# ---------------------------------------------------------------------------

class TaskLog(Base):
    __tablename__ = "task_logs"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id    = Column(UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    level      = Column(Enum(LogLevel), default=LogLevel.INFO)
    node       = Column(String(64), nullable=True)    # Which agent node emitted this
    message    = Column(Text, nullable=False)
    log_metadata = Column(JSONB, nullable=True)          # Arbitrary structured data
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    task       = relationship("Task", back_populates="logs")

    def __repr__(self) -> str:
        return f"<TaskLog task={self.task_id} [{self.level}] {self.message[:60]}>"


# ---------------------------------------------------------------------------
# FixAttempt — Records each generation + validation cycle
# ---------------------------------------------------------------------------

class FixAttempt(Base):
    __tablename__ = "fix_attempts"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id         = Column(UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    iteration       = Column(Integer, nullable=False)

    # What the LLM produced
    diff_patch      = Column(Text, nullable=True)       # unified diff
    files_modified  = Column(JSONB, nullable=True)       # list of file paths
    llm_reasoning   = Column(Text, nullable=True)        # chain-of-thought

    # Test results
    status          = Column(Enum(FixStatus), default=FixStatus.GENERATED)
    test_output     = Column(Text, nullable=True)
    tests_passed    = Column(Integer, nullable=True)
    tests_failed    = Column(Integer, nullable=True)
    error_message   = Column(Text, nullable=True)

    created_at      = Column(DateTime(timezone=True), server_default=func.now())

    task            = relationship("Task", back_populates="fix_attempts")

    def __repr__(self) -> str:
        return f"<FixAttempt task={self.task_id} iter={self.iteration} status={self.status}>"
