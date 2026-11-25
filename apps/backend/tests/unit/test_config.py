from __future__ import annotations

import pytest

from app.core.config import ConfigError, load_settings
from app.main import create_app

VALID_URL = "postgresql+asyncpg://u:p@localhost:5432/db"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("DATABASE_URL", "PROVIDER_MODE", "LOG_LEVEL"):
        monkeypatch.delenv(name, raising=False)


def test_missing_database_url_names_the_variable() -> None:
    with pytest.raises(ConfigError) as exc:
        load_settings(_env_file=None)
    assert "DATABASE_URL: is required but not set" in str(exc.value)
    assert ".env.example" in str(exc.value)


def test_aws_provider_mode_is_rejected_with_a_fix() -> None:
    with pytest.raises(ConfigError) as exc:
        load_settings(_env_file=None, database_url=VALID_URL, provider_mode="aws")
    message = str(exc.value)
    assert "PROVIDER_MODE" in message
    assert "PROVIDER_MODE=mock" in message


def test_non_asyncpg_url_is_rejected() -> None:
    with pytest.raises(ConfigError, match="postgresql\\+asyncpg://"):
        load_settings(_env_file=None, database_url="postgres://u:p@localhost/db")


def test_app_startup_fails_on_invalid_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", VALID_URL)
    monkeypatch.setenv("PROVIDER_MODE", "aws")
    monkeypatch.chdir("/")  # make sure no local .env file is picked up
    with pytest.raises(ConfigError):
        create_app()


def test_defaults_are_mock_mode() -> None:
    settings = load_settings(_env_file=None, database_url=VALID_URL)
    assert settings.provider_mode == "mock"


@pytest.mark.parametrize(
    ("field", "value", "name"),
    [
        ("retrieval_top_k", 0, "RETRIEVAL_TOP_K"),
        ("retrieval_top_k", 51, "RETRIEVAL_TOP_K"),
        ("retrieval_max_distance", 0, "RETRIEVAL_MAX_DISTANCE"),
        ("retrieval_max_distance", 2.5, "RETRIEVAL_MAX_DISTANCE"),
    ],
)
def test_retrieval_settings_are_bounded(field: str, value: float, name: str) -> None:
    with pytest.raises(ConfigError, match=name):
        load_settings(_env_file=None, database_url=VALID_URL, **{field: value})
