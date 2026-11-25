"""Validated application configuration.

Settings come from environment variables (and an optional `.env` file). Invalid
configuration raises `ConfigError` with a message that names each bad variable,
so startup fails early instead of failing on the first request.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _repo_root() -> Path:
    # apps/backend/app/core/config.py -> repository root. Inside the container the
    # file lives at /app/app/core/config.py, where DATASETS_DIR is set explicitly.
    parents = Path(__file__).resolve().parents
    return parents[4] if len(parents) > 4 else Path("/")


REPO_ROOT = _repo_root()


class ConfigError(RuntimeError):
    """Raised when configuration is missing or invalid."""


class Settings(BaseSettings):
    # Values come from the process environment (docker-compose.yml sets them).
    model_config = SettingsConfigDict(extra="ignore")

    database_url: str = Field(
        description="SQLAlchemy URL, e.g. postgresql+asyncpg://user:pass@host:5432/db",
    )
    provider_mode: Literal["mock", "aws"] = "mock"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    db_connect_timeout_seconds: float = Field(default=2.0, gt=0, le=30)
    readiness_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    datasets_dir: Path = REPO_ROOT / "datasets"
    # Retrieval (phase 2D). Cosine distance ranges from 0 (same direction) to 2.
    # Chunks farther than the cap are not evidence; if none are left, retrieval is
    # empty and callers must abstain. Tune with a real embedding model: mock
    # vectors have no meaning, so only near-identical text falls under the cap.
    retrieval_top_k: int = Field(default=5, ge=1, le=50)
    retrieval_max_distance: float = Field(default=0.8, gt=0, le=2)
    # Each channel (vector, lexical) returns this many candidates before fusion.
    retrieval_candidates: int = Field(default=20, ge=1, le=200)

    @field_validator("database_url")
    @classmethod
    def _require_asyncpg(cls, value: str) -> str:
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError("must start with 'postgresql+asyncpg://'")
        return value

    @field_validator("provider_mode")
    @classmethod
    def _reject_aws(cls, value: str) -> str:
        if value == "aws":
            raise ValueError(
                "'aws' is not supported yet: AWS providers arrive in a later milestone. "
                "Set PROVIDER_MODE=mock for local development."
            )
        return value


def _format_errors(exc: ValidationError) -> str:
    lines = ["Invalid configuration:"]
    for err in exc.errors():
        name = "_".join(str(part) for part in err["loc"]).upper() or "SETTINGS"
        message = "is required but not set" if err["type"] == "missing" else err["msg"]
        lines.append(f"  - {name}: {message}")
    lines.append("See .env.example and README.md (Configuration) for supported variables.")
    return "\n".join(lines)


def load_settings(**overrides: object) -> Settings:
    """Load settings, converting validation failures into a readable ConfigError."""
    try:
        return Settings(**overrides)  # type: ignore[arg-type]
    except ValidationError as exc:
        raise ConfigError(_format_errors(exc)) from None
