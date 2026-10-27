"""Refuse to serve when the database role would bypass Row-Level Security.

Postgres does not apply RLS to a superuser or a ``BYPASSRLS`` role, even with
``FORCE ROW LEVEL SECURITY``, and a role that owns a table can disable RLS on
it or drop its policy. Membership counts: a member of the owning role has its
rights, and a member of a superuser or ``BYPASSRLS`` role can ``SET ROLE`` to
it. A service connected as any of these runs without tenant
isolation and nothing says so. :func:`assert_rls_enforced` runs at startup
(``database.rls_startup_guard``) and raises :class:`RLSBypassError` so the
service exits instead.

No-op on non-Postgres dialects, where the RLS migration is a no-op too.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# One row for the session role. Roles it is a member of count as its own: it
# inherits a table owner's rights (and the owner skips RLS) and can SET ROLE to
# a superuser or BYPASSRLS role. Columns: the role, whether it is or can become
# a superuser, a BYPASSRLS role, and how many tables and partitioned tables
# outside the system and temporary schemas it or a role it belongs to owns
# (a temporary table holds no tenant rows and lasts one session).
ROLE_CHECK_SQL = """
SELECT current_user,
       EXISTS (SELECT FROM pg_roles s
                WHERE s.rolsuper AND pg_has_role(current_user, s.oid, 'MEMBER')),
       EXISTS (SELECT FROM pg_roles b
                WHERE b.rolbypassrls AND pg_has_role(current_user, b.oid, 'MEMBER')),
       (SELECT count(*)
          FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE pg_has_role(current_user, c.relowner, 'MEMBER')
           AND c.relkind IN ('r', 'p')
           AND n.nspname NOT IN ('pg_catalog', 'information_schema')
           AND n.nspname NOT LIKE 'pg_toast%'
           AND n.nspname NOT LIKE 'pg_temp%')
"""


class RLSBypassError(RuntimeError):
    """The service's database role is not subject to Row-Level Security."""


@dataclass(frozen=True, slots=True)
class RoleStatus:
    """What :data:`ROLE_CHECK_SQL` reports for the session role."""

    name: str
    superuser: bool
    bypass_rls: bool
    owned_tables: int

    def problems(self) -> list[str]:
        """Why RLS would not hold for this role; empty when it does."""
        found: list[str] = []
        if self.superuser:
            found.append("is or belongs to a superuser")
        if self.bypass_rls:
            found.append("has or belongs to a role with BYPASSRLS")
        if self.owned_tables:
            found.append(f"owns or belongs to the owner of {self.owned_tables} table(s)")
        return found


def check_role(status: RoleStatus) -> None:
    """Raise :class:`RLSBypassError` when ``status`` shows RLS would not hold."""
    problems = status.problems()
    if not problems:
        return
    raise RLSBypassError(
        f"database role {status.name!r} {', '.join(problems)}, so Row-Level "
        "Security does not isolate tenants. Connect the service as a role that "
        "is NOSUPERUSER NOBYPASSRLS, owns no tables and is a member of no role "
        "that is or does (database.rls_app_role "
        "provisions one), or turn off database.rls_startup_guard."
    )


async def assert_rls_enforced(engine: Any) -> bool:
    """Check the role ``engine`` connects as; raise when it bypasses RLS.

    ``engine`` is an SQLAlchemy ``AsyncEngine``. Returns ``True`` after a
    Postgres check passes, ``False`` on other dialects (nothing to check).
    """
    if engine.dialect.name != "postgresql":
        return False
    from sqlalchemy import text  # noqa: PLC0415 — generated-project runtime dep

    async with engine.connect() as conn:
        row = (await conn.execute(text(ROLE_CHECK_SQL))).one()
    status = RoleStatus(
        name=str(row[0]),
        superuser=bool(row[1]),
        bypass_rls=bool(row[2]),
        owned_tables=int(row[3]),
    )
    check_role(status)
    logger.info("tenant_rls_role_checked", extra={"role": status.name})
    return True


__all__ = [
    "ROLE_CHECK_SQL",
    "RLSBypassError",
    "RoleStatus",
    "assert_rls_enforced",
    "check_role",
]
