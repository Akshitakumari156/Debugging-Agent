# 🤖 AutoDebug — Autonomous GitHub Issue Resolution Agent

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-green.svg)](https://fastapi.tiangolo.com)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.1+-purple.svg)](https://langchain-ai.github.io/langgraph/)
[![Docker](https://img.shields.io/badge/Docker-Required-blue.svg)](https://docker.com)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> **AutoDebug** is a production-ready AI agent that autonomously clones GitHub repositories, reproduces bugs, identifies root causes, generates fixes, and opens Pull Requests — all without human intervention.

---

## 🏗️ Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                        Frontend (React)                      │
│            Real-time dashboard · Logs · PR link              │
└─────────────────────┬───────────────────────────────────────┘
                      │ HTTP/WebSocket
┌─────────────────────▼───────────────────────────────────────┐
│                    FastAPI Backend                            │
│     /run-task  /task-status  /logs  /ws/{task_id}            │
└──────┬──────────────────────────────────┬────────────────────┘
       │ Enqueue                           │ Read/Write
┌──────▼──────┐                   ┌───────▼────────┐
│    Redis    │                   │   PostgreSQL    │
│ Task Queue  │                   │  Logs/History   │
└──────┬──────┘                   └────────────────┘
       │ Dequeue
┌──────▼──────────────────────────────────────────────────────┐
│                   LangGraph Agent                            │
│                                                              │
│  Planner → RepoAnalyzer → BugReproducer → FixGenerator      │
│      ↑                                           │           │
│  Reflector ← TestRunner ← PRCreator ←────────────┘          │
└──────────────────────┬──────────────────────────────────────┘
                       │ Executes inside
┌──────────────────────▼──────────────────────────────────────┐
│                  Docker Sandbox                              │
│   Isolated container · CPU/Memory limits · Timeout          │
│   git clone · pip install · pytest · git push               │
└─────────────────────────────────────────────────────────────┘
```

---

## 📁 Project Structure

```
autodebug/
├── agent/              # LangGraph workflows & nodes
│   ├── graph.py        # Main agent graph definition
│   ├── nodes/          # Individual agent nodes
│   │   ├── planner.py
│   │   ├── repo_analyzer.py
│   │   ├── bug_reproducer.py
│   │   ├── fix_generator.py
│   │   ├── test_runner.py
│   │   ├── pr_creator.py
│   │   └── reflector.py
│   ├── tools/          # Agent tools (terminal, git, file editor)
│   │   ├── terminal.py
│   │   ├── file_editor.py
│   │   ├── git_tool.py
│   │   └── log_reader.py
│   └── state.py        # Shared agent state schema
├── sandbox/            # Docker sandbox management
│   ├── manager.py      # Container lifecycle
│   ├── executor.py     # Command execution in container
│   └── Dockerfile      # Sandbox image
├── github/             # GitHub API integration
│   ├── client.py       # PyGithub wrapper
│   ├── pr_manager.py   # PR creation/management
│   └── repo_cloner.py  # Repo cloning logic
├── backend/            # FastAPI application
│   ├── main.py         # App entrypoint
│   ├── routers/        # API route handlers
│   │   ├── tasks.py
│   │   └── logs.py
│   ├── worker.py       # Background task worker
│   └── ws_manager.py   # WebSocket manager
├── db/                 # Database layer
│   ├── models.py       # SQLAlchemy models
│   ├── database.py     # Connection + session
│   └── migrations/     # Alembic migrations
├── frontend/           # React dashboard
│   ├── src/
│   │   ├── App.jsx
│   │   ├── components/
│   │   └── pages/
│   └── package.json
├── docker-compose.yml  # Full stack orchestration
├── .env.example        # Environment variable template
└── requirements.txt    # Python dependencies
```

---

## ⚡ Quick Start

### Prerequisites

- Docker & Docker Compose
- Python 3.11+
- Node.js 18+
- OpenAI API key
- GitHub Personal Access Token

### 1. Clone & Configure

```bash
git clone https://github.com/your-org/autodebug
cd autodebug
cp .env.example .env
# Edit .env with your API keys
```

### 2. Start Infrastructure

```bash
docker-compose up -d postgres redis
```

### 3. Run Database Migrations

```bash
cd backend
alembic upgrade head
```

### 4. Start Backend

```bash
pip install -r requirements.txt
uvicorn backend.main:app --reload --port 8000
```

### 5. Start Frontend

```bash
cd frontend
npm install
npm run dev
```

### 6. Submit a Task

```bash
curl -X POST http://localhost:8000/run-task \
  -H "Content-Type: application/json" \
  -d '{
    "repo_url": "https://github.com/example/project",
    "issue_id": 42
  }'
```

---

## 🔧 Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `OPENAI_API_KEY` | OpenAI API key for GPT-4o | ✅ |
| `ANTHROPIC_API_KEY` | Anthropic API key for Claude | Optional |
| `GITHUB_TOKEN` | GitHub PAT with repo scope | ✅ |
| `DATABASE_URL` | PostgreSQL connection string | ✅ |
| `REDIS_URL` | Redis connection string | ✅ |
| `SANDBOX_CPU_LIMIT` | Docker CPU limit per task | Default: 1.0 |
| `SANDBOX_MEMORY_LIMIT` | Docker memory limit per task | Default: 512m |
| `SANDBOX_TIMEOUT` | Max seconds per task | Default: 300 |
| `MAX_FIX_ITERATIONS` | Max retry loops | Default: 3 |

---

## 🤖 Agent Workflow

```
Input (repo + issue)
        │
        ▼
   [Planner] ──── Breaks issue into steps, identifies files
        │
        ▼
 [Repo Analyzer] ─ Clones repo, detects language, maps structure
        │
        ▼
 [Bug Reproducer] ─ Runs tests, captures failure/stack trace
        │
        ▼
 [Fix Generator] ── LLM analyzes root cause, generates patch
        │
        ▼
  [Test Runner] ─── Validates fix, checks for regressions
        │
      pass? ──No──► [Reflector] ──► back to Fix Generator (max 3x)
        │
       Yes
        │
        ▼
  [PR Creator] ──── Branch + commit + PR with description
        │
        ▼
     Done ✅
```

---

## 📊 API Reference

### `POST /run-task`
Submit a new debugging task.

```json
{
  "repo_url": "https://github.com/owner/repo",
  "issue_id": 42,
  "issue_description": "Optional manual description"
}
```

### `GET /task-status/{task_id}`
Get current task status and progress.

### `GET /logs/{task_id}`
Stream all logs for a task.

### `WebSocket /ws/{task_id}`
Real-time log streaming.

---

## 🔒 Security

- All code execution happens inside Docker containers
- Containers have no access to host filesystem or network beyond GitHub
- CPU/memory/time limits enforced per task
- GitHub token scoped to minimum required permissions
- No secrets ever passed into sandbox containers

---

## 📄 License

MIT License — see [LICENSE](LICENSE)
