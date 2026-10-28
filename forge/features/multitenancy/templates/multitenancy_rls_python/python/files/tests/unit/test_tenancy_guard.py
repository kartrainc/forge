"""Unit tests for the RLS startup guard (``database.rls_startup_guard``)."""

from __future__ import annotations

import re
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.tenancy.guard import (
    RLSBypassError,
    RoleStatus,
    assert_rls_enforced,
    check_role,
)


def test_role_that_rls_applies_to_passes() -> None:
    check_role(RoleStatus(name="svc_app", superuser=False, bypass_rls=False, owned_tables=0))


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (RoleStatus("postgres", True, False, 0), "is or belongs to a superuser"),
        (RoleStatus("svc", False, True, 0), "has or belongs to a role with BYPASSRLS"),
        (RoleStatus("svc", False, False, 3), "owns or belongs to the owner of 3 table(s)"),
    ],
)
def test_role_that_bypasses_rls_is_refused(status: RoleStatus, expected: str) -> None:
    with pytest.raises(RLSBypassError, match=re.escape(expected)):
        check_role(status)


def test_every_problem_is_reported() -> None:
    status = RoleStatus("postgres", True, True, 2)
    assert status.problems() == [
        "is or belongs to a superuser",
        "has or belongs to a role with BYPASSRLS",
        "owns or belongs to the owner of 2 table(s)",
    ]


@pytest.mark.asyncio
async def test_non_postgres_engine_is_not_checked() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        assert await assert_rls_enforced(engine) is False
    finally:
        await engine.dispose()


class _Result:
    def __init__(self, row: tuple[Any, ...]) -> None:
        self._row = row

    def one(self) -> tuple[Any, ...]:
        return self._row


class _Conn:
    def __init__(self, row: tuple[Any, ...]) -> None:
        self._row = row

    async def __aenter__(self) -> _Conn:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def execute(self, _statement: object) -> _Result:
        return _Result(self._row)


class _Dialect:
    name = "postgresql"


class _Engine:
    dialect = _Dialect()

    def __init__(self, row: tuple[Any, ...]) -> None:
        self._row = row

    def connect(self) -> _Conn:
        return _Conn(self._row)


@pytest.mark.asyncio
async def test_postgres_role_that_rls_applies_to_passes() -> None:
    assert await assert_rls_enforced(_Engine(("svc_app", False, False, 0))) is True


@pytest.mark.asyncio
async def test_postgres_superuser_stops_startup() -> None:
    with pytest.raises(RLSBypassError, match="'postgres' is or belongs to a superuser"):
        await assert_rls_enforced(_Engine(("postgres", True, False, 5)))
