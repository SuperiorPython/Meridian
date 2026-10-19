import pytest

from meridian.config import get_settings


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    """Keep a developer's real .env (token, env, paths) from changing test behaviour.
    An empty environment value overrides the .env file."""
    monkeypatch.setenv("MERIDIAN_ENV", "dev")
    monkeypatch.setenv("MERIDIAN_API_TOKEN", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
