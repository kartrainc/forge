"""``database.multitenancy`` — tenant-isolation discriminator + resolution knobs.

The discriminator controls *how strongly* tenants are isolated in the
generated persistence layer:

- ``none`` (default): INERT. No new fragments, no output change. The base
  python-service-template is already tenant-*aware* via weld's
  ``TenantMixin`` / ``customer_id`` columns + ``TenantScopedRepository``;
  ``none`` adds no enforcement on top of that application-layer scoping.
- ``shared_rls``: Postgres Row-Level Security. One shared schema, one
  shared database; every tenant-scoped table gets ``ENABLE ROW LEVEL
  SECURITY`` + a ``USING (customer_id = current_setting('app.current_tenant'))``
  policy, and each request/transaction binds the GUC via a session hook.
  Python + Go (Postgres-only).
- ``schema_per_tenant``: per-tenant Postgres schema. Each tenant's tables
  live in their own ``tenant_<id>`` schema; a request middleware resolves the
  tenant and a session ``begin`` hook binds ``SET LOCAL search_path`` to that
  schema, so unqualified queries physically touch only one tenant's data.
  Python tier-1 (Postgres-only). Ships ``provision_tenant_schema`` to clone
  the table set into a new tenant schema.
- ``db_per_tenant``: KNOWN but DEFERRED. Validation accepts it (so a
  forge.toml that pins it is not rejected outright), but the resolver raises a
  clear "not yet implemented" error rather than silently generating a project
  with no isolation. See the resolver guard in
  ``forge/capability_resolver.py`` (``_check_multitenancy_deferred``).

Resolution sub-options drive *how* the per-request tenant id is discovered.
They compose with the just-landed auth ``ClaimMapper`` seam: with
``tenant_resolution=token_claim`` the GUC binder reads the tenant id from the
verified JWT claims using the same dot-path machinery the OIDC provider's
``ClaimMapper`` uses (``tenant_claim_path``); ``header`` reads a gateway-
injected header (``tenant_header_name``); ``subdomain`` parses the Host header.
"""

from __future__ import annotations

from forge.api import ForgeAPI
from forge.config import BackendLanguage
from forge.options._registry import (
    FeatureCategory,
    Option,
    OptionType,
)


def register_all(api: ForgeAPI) -> None:
    api.add_option(
        Option(
            path="database.multitenancy",
            type=OptionType.ENUM,
            default="none",
            options=("none", "shared_rls", "schema_per_tenant", "db_per_tenant"),
            summary="Tenant-isolation strategy for the generated persistence layer.",
            description="""\
Discriminator for how strongly tenants are isolated in the database.

- ``none`` (default): inert — no enforcement fragment is added. The base
  template stays tenant-*aware* (weld ``TenantMixin`` / ``customer_id``
  columns + ``TenantScopedRepository`` application-layer scoping) but no
  database-enforced isolation is layered on. Byte-identical to a project
  that never set this option.
- ``shared_rls``: Postgres Row-Level Security. One shared database + schema;
  every ``customer_id``-bearing table gets ``ENABLE ROW LEVEL SECURITY`` +
  a ``USING (customer_id = current_setting('app.current_tenant')::uuid)``
  policy (idempotent migration). A request middleware resolves the tenant
  (token claim / header / subdomain) and a session GUC hook binds
  ``app.current_tenant`` per transaction, so the database itself rejects
  cross-tenant reads/writes. Layers ON TOP of the existing TenantMixin —
  it adds the RLS policy + GUC binding + resolver, it does NOT re-add the
  ``customer_id`` column.
- ``schema_per_tenant``: per-tenant Postgres schema. Each tenant's tables live
  in their own ``tenant_<id>`` schema; ``SET LOCAL search_path`` routes each
  transaction so unqualified queries physically touch only one tenant's data.
  Bound by two composed seams: an engine ``begin`` listener (fail-closed default
  for every session — binds the edge-resolved header/subdomain tenant or ``''``)
  plus a post-auth UoW binder that, for ``token_claim``, overrides with the
  authenticated account's schema. **Fail-closed** (unbound ⇒ ``search_path=''``,
  not ``public``). Ships ``provision_tenant_schema`` to clone the table set into
  a new tenant schema.
- ``db_per_tenant``: recognised value but NOT yet implemented (separate
  databases / connection pools per tenant). forge accepts it in a forge.toml
  without rejecting the whole config, but generation fails with an explicit
  "not yet implemented" error rather than silently producing an un-isolated
  project. Use ``shared_rls`` or ``schema_per_tenant`` today.

BACKENDS: python (shared_rls, schema_per_tenant), go (shared_rls). Go ships
``shared_rls`` as a goose migration with the same policy on ``items`` plus a pgx
transaction scope that binds ``app.current_tenant`` with ``set_config(..., true)``
for every items-repository call; project tables add the policy in the migration
that creates them. Without a verified token (``auth.mode=none`` or the
platform-auth dev mode) a Go request's header/subdomain tenant becomes its
identity's tenant and must be a UUID (400 otherwise). ``schema_per_tenant`` is
Python-only.
ENGINE: postgres. The GUC / search_path hooks are no-ops on non-Postgres
dialects.""",
            category=FeatureCategory.PLATFORM,
            requires_database=True,
            # The non-``none`` strategies ship Python (and, for shared_rls, Go)
            # realisations. ``allowed_backends`` is only enforced for an ACTIVE
            # value (``is_active_value`` — i.e. a value whose ``enables`` is
            # non-empty); ``none`` maps to no fragments so the default never
            # trips the check. Setting it here lets the resolver +
            # ProjectConfig walker reject e.g. ``shared_rls`` on a Node/Rust
            # project with a clear message instead of a runtime no-op.
            # ``schema_per_tenant`` on a Go-only project is rejected by the
            # resolver's ``_VALUE_REQUIRES_BACKEND`` table.
            allowed_backends=(BackendLanguage.PYTHON, BackendLanguage.GO),
            enables={
                "shared_rls": ("multitenancy_rls_python", "multitenancy_rls_go"),
                "schema_per_tenant": ("multitenancy_schema_per_tenant_python",),
                # ``db_per_tenant`` intentionally maps to no fragment — it is
                # recognised-but-deferred (separate databases / connection pools
                # per tenant). The resolver's ``_check_multitenancy_deferred``
                # guard turns a user selection into an explicit OptionsError so
                # the value never silently no-ops.
            },
        )
    )

    api.add_option(
        Option(
            path="database.tenant_resolution",
            type=OptionType.ENUM,
            default="token_claim",
            options=("token_claim", "header", "subdomain"),
            summary="How the per-request tenant id is discovered for isolation binding.",
            description="""\
Drives the ``TenantResolver`` shipped by the non-``none`` strategies —
``shared_rls`` (binds the resolved id to the RLS GUC) and ``schema_per_tenant``
(routes the connection's ``search_path`` to the tenant schema). The resolver is
identical across both; only the binding mechanism differs. Inert when
``database.multitenancy=none``.

- ``token_claim`` (default): read the tenant id from the verified JWT claims
  via a dot-path (``database.tenant_claim_path``), reusing the auth
  ``ClaimMapper`` seam the OIDC / in_memory providers ship. The middleware
  reads ``request.state.identity`` (bound by the platform-auth middleware)
  and extracts the configured claim. Go reads the platform-auth SDK's
  verified claims and falls back to the bound identity's tenant.
- ``header``: read the tenant id from a gateway-injected request header
  (``database.tenant_header_name``). For deployments where an upstream
  proxy / API gateway already resolved + validated the tenant.
- ``subdomain``: parse the leftmost label of the request Host header
  (``acme.example.com`` → ``acme``). For per-tenant subdomain routing. A
  single label (``localhost``) or an IP host carries none.

With authentication, a header or subdomain tenant must be the verified
identity's (403 otherwise): its tenant id, or for a label that is not a UUID,
its tenant slug, ignoring case; the identity's tenant id is then bound.
Without a verified identity a label that is not a UUID binds no tenant
(``shared_rls``). With Keycloak organizations the slug is the organization's
alias, so each tenant's host label must equal its organization's alias. The
seeded development organization is ``default`` while Compose serves the app on
``app.localhost``, so with ``subdomain`` the development user is refused until
the alias and the host agree. With ``auth.keycloak_tenant_source=user_attribute``
there is no slug, so only UUID labels are accepted.

BACKENDS: python, go. Inert unless ``database.multitenancy != none``.""",
            category=FeatureCategory.PLATFORM,
            requires_database=True,
        )
    )

    api.add_option(
        Option(
            path="database.tenant_claim_path",
            type=OptionType.STR,
            default="tenant_id",
            summary="Dot-path to the tenant id within the verified token claims.",
            description="""\
Used when ``database.tenant_resolution=token_claim``. A dot-path traversed
by the auth ``ClaimMapper`` (``organization.id`` reads
``claims['organization']['id']``; a literal URL-shaped claim name like
``https://example.com/tenant`` is matched as a whole key first). Defaults to
``tenant_id`` to match the platform-auth SDK's default tenant claim.

BACKENDS: python, go. Written into the generated ``TenantResolver`` config;
no fragment is keyed off the value.""",
            category=FeatureCategory.PLATFORM,
            requires_database=True,
        )
    )

    api.add_option(
        Option(
            path="database.tenant_header_name",
            type=OptionType.STR,
            default="X-Tenant-ID",
            summary="Request header carrying the tenant id (header resolution).",
            description="""\
Used when ``database.tenant_resolution=header``. The HTTP header the
``TenantResolver`` reads the tenant id from (case-insensitive). Defaults to
``X-Tenant-ID``.

BACKENDS: python, go. Written into the generated ``TenantResolver`` config;
no fragment is keyed off the value.""",
            category=FeatureCategory.PLATFORM,
            requires_database=True,
        )
    )

    api.add_option(
        Option(
            path="database.rls_app_role",
            type=OptionType.BOOL,
            default=True,
            summary="Run shared_rls services as a dedicated role that cannot bypass RLS.",
            description="""\
Postgres skips Row-Level Security for superusers and ``BYPASSRLS`` roles, even
with ``FORCE ROW LEVEL SECURITY``. When on, each ``shared_rls`` backend keeps
the owner role for its migrations only and serves requests as
``<db_name>_app``: ``LOGIN NOSUPERUSER NOBYPASSRLS``, owning no tables, with
SELECT/INSERT/UPDATE/DELETE on the tables and USAGE on the sequences of
``public``. Migrations run as the owner, and ``deploy/compose/db-app-role.sh``
then creates the role and refreshes its grants: the ``<backend>-db-role``
one-shot in Compose; in Helm the pre-install migrate Job, or with
``infra.inCluster`` the Deployment's init containers, so the role exists before
the service starts. The service no longer migrates on start
(``MIGRATE_ON_START=false``). The generated CI gets a ``tenant-isolation`` job
that migrates a Postgres service container, runs the script and runs the
isolation tests as the role.

Turn it off when the deployment already provides such a role; the service's
database URL must then point at it, or ``database.rls_startup_guard`` stops the
service.

Node backends get the role too: forge enforces no RLS in them, but their own
policies (Prisma migrations) only bind a role that does not own the tables.

BACKENDS: python, go, node. Inert unless ``database.multitenancy=shared_rls``.""",
            category=FeatureCategory.PLATFORM,
            # Inert unless database.multitenancy=shared_rls, which itself
            # requires a database; true defaults must not reject database.mode=none.
            requires_database=False,
        )
    )

    api.add_option(
        Option(
            path="database.rls_startup_guard",
            type=OptionType.BOOL,
            default=True,
            summary="Refuse to start a shared_rls service whose role would bypass RLS.",
            description="""\
When on, a ``shared_rls`` service checks its database role at startup and
exits when the role is a superuser, has ``BYPASSRLS``, or owns tables in the
database, itself or through a role it belongs to: in each case RLS is not
enforced, and isolation would be off without any sign. The generated isolation
tests run as a role RLS applies to and, under ``CI``, fail instead of skipping
when they get no Postgres or no such role (with ``database.rls_app_role`` the
CI ``tenant-isolation`` job provides both).

Turn it off only for a deployment that isolates tenants some other way.

BACKENDS: python, go. Inert unless ``database.multitenancy=shared_rls``.""",
            category=FeatureCategory.PLATFORM,
            # Inert unless database.multitenancy=shared_rls, which itself
            # requires a database; true defaults must not reject database.mode=none.
            requires_database=False,
        )
    )

    api.add_option(
        Option(
            path="database.allow_unverified_tenant",
            type=OptionType.BOOL,
            default=False,
            summary="Allow a header or subdomain tenant without a verified identity.",
            description="""\
With ``auth.mode=none`` nothing verifies who sends a request, so a tenant taken
from a header or the Host subdomain is whatever the client sends: anyone can
read any tenant's data, and in Go a new tenant per request also escapes the
rate limit. forge therefore refuses ``database.multitenancy`` ``shared_rls`` or
``schema_per_tenant`` with ``database.tenant_resolution`` ``header`` or
``subdomain`` and ``auth.mode=none``.

Set this to ``true`` only when a trusted gateway in front of the services
authenticates the caller and sets or strips the tenant header itself.

With authentication, a request with a verified identity may only name that
identity's tenant (403 otherwise), and a bearer token that does not verify is
answered 401 (503 when it cannot be verified now) rather than ignored. A route
that needs no sign-in, called without a token, still serves whichever tenant
the header or host names: keep tenant data that is not public behind routes
that require one.

BACKENDS: python, go. Checked at generation.""",
            category=FeatureCategory.PLATFORM,
            # Inert unless database.multitenancy=shared_rls, which itself
            # requires a database; true defaults must not reject database.mode=none.
            requires_database=False,
        )
    )
