"""Multitenancy fragments — Postgres tenant isolation for Python and Go backends.

Two strategies, one per ``database.multitenancy`` value:

- ``multitenancy_rls_python`` (``shared_rls``): one shared schema, Row-Level
  Security policies filter rows by the ``app.current_tenant`` GUC.
- ``multitenancy_schema_per_tenant_python`` (``schema_per_tenant``): one schema
  per tenant; a per-transaction ``search_path`` hook routes queries to the
  caller's ``tenant_<id>`` schema. No RLS migration (provisioned at runtime via
  ``provision_tenant_schema``).

Both share the same ``TenantResolver`` (token claim / header / subdomain),
request-middleware + engine-``begin``-listener shape, and the
``excluded_app_templates=("tenant-management-service",)`` exemption (the TMS
control plane isolates by Keycloak realm, not the DB layer). They are mutually
exclusive (a single enum value selects exactly one), so they never both apply.

``multitenancy_rls_python`` realises ``database.multitenancy=shared_rls``. It
ships, per Python backend:

- a ``TenantResolver`` seam (``app/core/tenancy/resolver.py``) — resolve the
  per-request tenant id from a verified token claim (reusing the auth
  ``ClaimMapper`` dot-path seam), a request header, or the Host subdomain,
  driven by ``database.tenant_resolution`` + ``database.tenant_claim_path`` +
  ``database.tenant_header_name``;
- a ``TenantRLSHook`` seam (``app/core/tenancy/rls.py``) — bind the resolved
  tenant id to the Postgres ``app.current_tenant`` GUC for the lifetime of a
  session/transaction, with the GUC name a module constant
  (``TENANT_GUC``) and a no-op on non-Postgres dialects;
- request middleware (``app/middleware/tenant_rls.py``) — extract the tenant
  via the resolver and stash it for the GUC hook;
- an idempotent Alembic migration (``alembic/versions/0002_enable_rls.py``)
  that enables RLS + creates the per-table tenant policy via a re-runnable
  macro (``CREATE POLICY`` guarded by ``DROP POLICY IF EXISTS``,
  ``ENABLE ROW LEVEL SECURITY`` guarded by ``pg_class.relrowsecurity``).

The fragment reads ``database.tenant_resolution`` / ``database.tenant_claim_path``
/ ``database.tenant_header_name`` so the rendered resolver is configured for
the project's chosen strategy. It is backend-scoped + Python-only (parity tier
auto-derives to 3); the GUC-binding + Alembic RLS pattern targets the
SQLAlchemy/Alembic stack the python-service-template ships.

``multitenancy_rls_go`` realises ``shared_rls`` on Go backends with the same
policy, GUC, options and env vars: a goose migration, a pgx transaction scope
that binds ``app.current_tenant`` with ``set_config(..., true)``, and the
resolver + middleware. ``schema_per_tenant`` stays Python-only.
"""

from __future__ import annotations

from pathlib import Path

from forge.api import ForgeAPI
from forge.config import BackendLanguage
from forge.fragments._spec import Fragment, FragmentImplSpec

_TEMPLATES = Path(__file__).resolve().parent / "templates"


def _impl(name: str, lang: str) -> str:
    return str(_TEMPLATES / name / lang)


def register_all(api: ForgeAPI) -> None:
    api.add_fragment(
        Fragment(
            name="multitenancy_rls_python",
            implementations={
                BackendLanguage.PYTHON: FragmentImplSpec(
                    fragment_dir=_impl("multitenancy_rls_python", "python"),
                    # Backend-scoped (default) — files land per Python backend.
                    env_vars=(
                        # The GUC name is a constant in code (TENANT_GUC) but
                        # surfaced as env for ops visibility / override.
                        ("TENANT_RLS_GUC", "app.current_tenant"),
                    ),
                    # The rendered resolver + middleware are configured from
                    # the chosen resolution strategy + claim/header names.
                    reads_options=(
                        "database.tenant_resolution",
                        "database.tenant_claim_path",
                        "database.tenant_header_name",
                        "database.rls_startup_guard",
                        "database.rls_app_role",
                    ),
                ),
            },
            # postgres-pgvector is overkill; plain postgres is provisioned by
            # database.mode=generate already. RLS needs no extra sidecar.
            capabilities=(),
            # ``shared_rls`` is a project-global option, so the RLS fragment
            # would otherwise land on EVERY Python backend. The
            # ``tenant-management-service`` control-plane variant must be exempt:
            # it isolates tenants by Keycloak realm (not Postgres RLS) and ships
            # its own ``0002_tms_tables`` migration off ``0001`` — adding the RLS
            # ``0002`` would create a second Alembic head and break its boot.
            excluded_app_templates=("tenant-management-service",),
        )
    )

    # Go realisation of ``shared_rls``: ``internal/tenancy`` (settings, resolver,
    # set_config(..., true) transaction binding), the ``TenantRLS`` middleware,
    # the ``00099_enable_rls`` goose migration, and the items repository scope
    # wired at ``FORGE:REPOSITORY_SCOPE``. Applied after the Go platform-auth
    # middleware so ``TenantRLS`` sits inside it and sees the verified token.
    api.add_fragment(
        Fragment(
            name="multitenancy_rls_go",
            implementations={
                BackendLanguage.GO: FragmentImplSpec(
                    fragment_dir=_impl("multitenancy_rls_go", "go"),
                    env_vars=(("TENANT_RLS_GUC", "app.current_tenant"),),
                    # ``auth.mode`` selects whether token claims come from the
                    # platform-auth SDK (generate) or are absent (none).
                    reads_options=(
                        "database.tenant_resolution",
                        "database.tenant_claim_path",
                        "database.tenant_header_name",
                        "database.rls_startup_guard",
                        "database.rls_app_role",
                        "auth.mode",
                    ),
                ),
            },
            capabilities=(),
            after=("platform_auth_go_middleware",),
        )
    )

    api.add_fragment(
        Fragment(
            name="multitenancy_schema_per_tenant_python",
            implementations={
                BackendLanguage.PYTHON: FragmentImplSpec(
                    fragment_dir=_impl("multitenancy_schema_per_tenant_python", "python"),
                    # Backend-scoped (default) — files land per Python backend.
                    env_vars=(
                        # The schema prefix is a code default (DEFAULT_SCHEMA_PREFIX)
                        # but surfaced as env for ops visibility / override. No
                        # forge option backs it (keeps generated forge.toml byte-
                        # identical for the off-by-default case).
                        ("TENANT_SCHEMA_PREFIX", "tenant_"),
                    ),
                    # The rendered resolver + middleware are configured from
                    # the chosen resolution strategy + claim/header names (same
                    # three knobs the shared_rls fragment reads).
                    reads_options=(
                        "database.tenant_resolution",
                        "database.tenant_claim_path",
                        "database.tenant_header_name",
                    ),
                ),
            },
            capabilities=(),
            # Same control-plane exemption as the RLS fragment: schema-per-tenant
            # is project-global, but the TMS variant isolates by Keycloak realm
            # and must not have its queries routed through per-tenant schemas.
            excluded_app_templates=("tenant-management-service",),
        )
    )
