"""
backend/config.py — Application-wide settings via pydantic-settings

All values are read from environment variables (or .env file).
"""

from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # LLM
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    GROQ_API_KEY: str = ""
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    MODEL: str = "gpt-4o"

    # GitHub
    GITHUB_TOKEN: str = ""
    GITHUB_BOT_NAME: str = "autodebug-bot"
    GITHUB_BOT_EMAIL: str = "autodebug@example.com"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://autodebug:autodebug@localhost:5432/autodebug"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # Sandbox
    SANDBOX_CPU_LIMIT: float = 1.0
    SANDBOX_MEMORY_LIMIT: str = "512m"
    SANDBOX_TIMEOUT: int = 300
    SANDBOX_IMAGE: str = "autodebug-sandbox:latest"
    SANDBOX_NETWORK: str = "autodebug-net"

    # Agent
    MAX_FIX_ITERATIONS: int = 3

    # Backend
    BACKEND_PORT: int = 8000
    CORS_ORIGINS: str | list[str] = ["http://localhost:3000", "http://localhost:5173"]
    SECRET_KEY: str = "change-me"
    LOG_LEVEL: str = "INFO"

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors(cls, v: str | list) -> list[str]:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",")]
        return v


settings = Settings()
