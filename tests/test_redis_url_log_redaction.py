"""Redis URLs carry the redis chart's password (deploy.helm_layout=per_service): no fragment logs or reports one whole."""

from __future__ import annotations

import importlib.util
import logging
import sys
import types
from pathlib import Path

import pytest

_TEMPLATES = Path(__file__).resolve().parents[1] / "forge" / "features"
_PY_RESPONSE_CACHE = (
    _TEMPLATES / "middleware/templates/response_cache/python/files/src/app/core/response_cache.py"
)
_GO_RESPONSE_CACHE_STORE = (
    _TEMPLATES
    / "middleware/templates/response_cache/go/files/internal/middleware/response_cache_redis.go"
)
_GO_HEALTH_DEEP = (
    _TEMPLATES / "observability/templates/enhanced_health/go/files/internal/routes/health_deep.go"
)

_PASSWORD = "hunter2-redis-password"


def _load_response_cache() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("_response_cache_under_test", _PY_RESPONSE_CACHE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stub_fastapi_cache(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """fastapi-cache2 and redis stand-ins: records what FastAPICache.init receives."""
    inits: list[object] = []
    package = types.ModuleType("fastapi_cache")
    package.FastAPICache = types.SimpleNamespace(  # type: ignore[attr-defined]
        init=lambda backend, prefix: inits.append(backend)
    )
    backends = types.ModuleType("fastapi_cache.backends")
    redis_backend = types.ModuleType("fastapi_cache.backends.redis")
    redis_backend.RedisBackend = lambda client: ("redis", client)  # type: ignore[attr-defined]
    redis_asyncio = types.ModuleType("redis.asyncio")
    redis_asyncio.from_url = lambda url, **_: url  # type: ignore[attr-defined]
    for name, module in (
        ("fastapi_cache", package),
        ("fastapi_cache.backends", backends),
        ("fastapi_cache.backends.redis", redis_backend),
        ("redis", types.ModuleType("redis")),
        ("redis.asyncio", redis_asyncio),
    ):
        monkeypatch.setitem(sys.modules, name, module)
    return inits


@pytest.mark.parametrize(
    "url,shown",
    [
        (f"redis://:{_PASSWORD}@redis:6379/1", "redis://:***@redis:6379/1"),
        (f"rediss://cache:{_PASSWORD}@redis.example:6380", "rediss://cache:***@redis.example:6380"),
        ("redis://redis:6379/1", "redis://redis:6379/1"),
    ],
)
def test_the_python_response_cache_logs_its_url_without_the_password(
    url: str, shown: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    inits = _stub_fastapi_cache(monkeypatch)
    monkeypatch.setenv("RESPONSE_CACHE_URL", url)
    module = _load_response_cache()
    with caplog.at_level(logging.INFO):
        module.setup_response_cache()
    # the client still connects with the password
    assert inits == [("redis", url)]
    assert _PASSWORD not in caplog.text
    assert f"redis @ {shown}" in caplog.text


def test_the_go_response_cache_never_reports_its_url() -> None:
    source = _GO_RESPONSE_CACHE_STORE.read_text(encoding="utf-8")
    # url.Parse's error quotes the whole url: only its cause is kept
    assert 'invalid redis url: %w", urlErr.Err' in source
    assert "parsed.Redacted()" in source


def test_the_go_deep_health_never_reports_its_redis_url() -> None:
    source = _GO_HEALTH_DEEP.read_text(encoding="utf-8")
    assert '"invalid REDIS_URL"' in source
    assert '%q", rawURL' not in source
