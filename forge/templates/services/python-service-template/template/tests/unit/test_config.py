"""Tests for configuration domain models."""

import pytest
from pydantic import ValidationError

from app.core.config.domain import AuditConfig, CorsConfig, DbConfig


class TestDbConfig:
    def test_defaults(self):
        cfg = DbConfig()
        assert cfg.pool_size == 10
        assert cfg.max_overflow == 20
        assert cfg.echo is False

    def test_pool_size_minimum(self):
        with pytest.raises(ValidationError):
            DbConfig(pool_size=0)

    def test_pool_timeout_minimum(self):
        with pytest.raises(ValidationError):
            DbConfig(pool_timeout=0)

    def test_custom_values(self):
        cfg = DbConfig(
            url="postgresql+asyncpg://localhost/test",
            pool_size=20,
            echo=True,
        )
        assert cfg.pool_size == 20
        assert cfg.echo is True


class TestCorsConfig:
    def test_defaults(self):
        cfg = CorsConfig()
        assert cfg.enabled is False
        assert "*" in cfg.allow_origins

    def test_enabled(self):
        cfg = CorsConfig(enabled=True, allow_origins=["http://localhost:3000"])
        assert cfg.enabled is True
        assert len(cfg.allow_origins) == 1


class TestAuditConfig:
    def test_defaults(self):
        cfg = AuditConfig()
        assert cfg.enabled is True
        assert "/health" in cfg.excluded_paths
        assert "OPTIONS" in cfg.excluded_methods

    def test_disabled(self):
        cfg = AuditConfig(enabled=False)
        assert cfg.enabled is False
