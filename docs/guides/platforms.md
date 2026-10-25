# Platform generator guide (`--platform`)

Most forge invocations scaffold one backend (and maybe a frontend). The
**platform presets** scaffold a *system*: domain services routed directly from
the edge, optional shared auth and service-to-service (S2S) trust, an event bus
where selected, and — for
the SaaS preset — per-tenant data isolation. One flag stands the whole topology
up:

```bash
forge --platform microservices --project-name shop
```

This guide covers which preset to pick, what each one assembles, how the
multi-service auth and multitenancy actually work, and — importantly — the
**dev-posture credentials you must rotate before deploying.**

> A preset is just the lowest-priority configuration layer. It is deep-merged
> *under* your CLI flags and `--config` file, so every preset value is a
> default you can override (`--set auth.provider=oidc_generic`, a custom
> `backends:` list, etc.). With no `--platform`, generation is byte-identical
> to the single-service path.

## Choosing a preset

`forge --platform` accepts four presets (the choices are discovered from
`forge/templates/platforms/*/platform.toml`):

| Preset | Shape | Keycloak | S2S auth | Multitenancy | Use it when |
| --- | --- | --- | --- | --- | --- |
| **`monolithic`** | 1 Python CRUD backend + Vue (sidebar) | no | no | no | You want the classic single-service app with a UI and no auth-server overhead. |
| **`microservices`** | Directly routed `orders` + `inventory` CRUD services + Vue, with an event bus; `orders` declares `inventory` as a dependency | yes | yes | no | You have services that call each other and emit events. |
| **`headless-api`** | One directly routed `orders` API with edge authentication, **no frontend** | yes | no | no | You want an authenticated API for mobile or third-party clients. |
| **`multitenant-saas`** | `tms` control plane + RLS-isolated `app` CRUD service + Vue, behind the Gatekeeper | yes | no | **shared-RLS** | You're building multi-tenant SaaS and need per-tenant row isolation + tenant provisioning. |

Everything except `monolithic` brings up Keycloak + the Gatekeeper edge-auth
stack + Redis — a substantial stack. Start with `monolithic` if you don't yet
need cross-service auth.

Built-in presets route directly to domain APIs. URL routing is handled by
Traefik; each application service owns its business logic and authorization.
The microservices preset still synthesizes S2S grants for `orders → inventory`.
This declares a callable dependency; the CRUD scaffold does not automatically
implement an ordering workflow. The headless preset has one backend, so S2S
discovery is disabled while edge authentication remains enabled.
The multitenant SaaS preset also leaves S2S discovery disabled: shared edge
authentication and tenant isolation do not imply synthesized service grants.

### Running the built-in microservices preset

A worked invocation:

```bash
forge --platform microservices --project-name shop --output-dir . --yes --no-docker
cd shop
docker compose up --build         # orders:5020, inventory:5030,
                                  # keycloak, gatekeeper, redis, postgres, vue
```

The generated `docker-compose.yml` is a flat monorepo (`services/`, `apps/`,
`sdks/`). Each backend gets its own image. The orders service declares inventory
as an allowed S2S target. Traefik routes `/api/orders/...` and
`/api/inventory/...` directly to the corresponding backend, with the configured
authentication middleware.

### Application composition and third-party access

Implement client-specific BFF behavior as application code when needed. Services
call their dependencies directly using [S2S credentials or user delegation](service-to-service.md).
API keys are optional and disabled in every preset. Add `auth.api_keys: true` to
a Gatekeeper-backed configuration to enable [scoped third-party access](api-keys.md).
The option does not add another application service.

An explicit `backends` list replaces a preset's entire list. Preserve the actual
service topology in the persisted configuration and review changes to URLs,
authorization and dependencies when upgrading. See [upgrade notes](../../UPGRADING.md#direct-routing-defaults).

## How multi-service auth works (synthesis)

When a preset sets `auth.service_discovery=true` and the project has more than
one backend, Forge generates an S2S credential registry
(`forge/synthesis/platform.py`). Two things are generated:

1. **A service registry** (`deploy/infra/gatekeeper/secrets/service_registry.yaml`) —
   one entry per backend with a `client_id` (`svc-<name>`), a client secret,
   and the set of audiences (callee → scopes) it may request. Those audience
   grants are derived directly from each backend's `depends_on` edges, so a
   service can only mint tokens for the peers it declared.
2. **Per-service env** injected into each compose service:
   `GATEKEEPER_CLIENT_ID` / `GATEKEEPER_CLIENT_SECRET` /
   `GATEKEEPER_TOKEN_ENDPOINT`, and an `INTERNAL_SERVICE_URL_<PEER>` for every
   in-network callee.

At runtime: a caller exchanges its client id+secret at the Gatekeeper's token
endpoint, specifying the target such as `svc-inventory` and the authorized
tenant ID, for a short-lived (≈5 min) ES256 JWT with the shared audience
`forge-services`; the callee verifies that JWT against the Gatekeeper's JWKS
endpoint and enforces service-specific scopes and tenant/resource permissions.
Target grants are not network policies or per-service JWT audience isolation. The Gatekeeper — not Keycloak — is the
sole internal issuer; Keycloak is the upstream identity provider for *end
users*. See [`docs/auth-architecture.md`](../auth-architecture.md) for the full
token/JWKS/BFF-session design.

> The synthesized service secrets are **deterministic** (derived from the
> project slug + service name) so `forge --update` is idempotent. That's a
> dev convenience, **not** production-grade randomness — see rotation below.

## Multitenancy (`multitenant-saas`)

The `multitenant-saas` preset sets `database.multitenancy=shared_rls` and
`database.tenant_resolution=token_claim`. The chain:

1. A Keycloak user carries a `tenant_id` attribute; a realm protocol-mapper
   projects it into the `https://forge/tenant_id` access-token claim.
2. The Gatekeeper verifies the user token and carries that claim into the
   internal JWT it mints.
3. The app's tenancy middleware resolves the tenant from the verified claim and
   binds it per-transaction with `SET LOCAL app.current_tenant = '<uuid>'`.
4. Postgres **row-level security** policies (`USING customer_id =
   current_setting('app.current_tenant')::uuid`) then filter every query to
   that tenant automatically — application code stays tenant-agnostic.

The generated RLS migrations cover the tables forge generates. Tables a project
adds need their own migration that enables and forces RLS with a tenant policy;
a Go service's `OPERATIONS.md` gives the SQL and the rules for data migrations
and rollbacks.

The `tms` (tenant-management-service) control plane provisions tenants (Keycloak
realm/user + the routing entries the Gatekeeper reads). It is **exempt** from
the RLS policy (it's listed in the RLS fragment's `excluded_app_templates`): TMS
isolates by *realm*, not by Postgres row, and keeps its own migration chain.

Resolution is **fail-closed**: an unbound tenant binds an empty scope and RLS
returns zero rows rather than leaking across tenants.

## Auth providers

`auth.provider` (meaningful when `auth.mode=generate`) selects the token
authority. No preset overrides it, so its registry default is `gatekeeper`; actual auth-stack emission also
depends on the project/preset and auth selection. Change
it with `--set auth.provider=<value>`:

| Provider | What it generates | For |
| --- | --- | --- |
| **`gatekeeper`** (default) | The Gatekeeper edge-auth service + Keycloak realm + the one-shot keygen and realm-sync sidecars | The full production-shaped stack. |
| **`in_memory`** | A zero-dependency in-process issuer that mints test JWTs | Local dev / tests. **Refuses to start under production posture.** |
| **`oidc_generic`** | OIDC-discovery wiring against an external issuer (Auth0, Cognito, Okta, …) | Bring-your-own IdP. |
| **`none`** | No token authority | Bring-your-own issuer / public service. |

## Security: dev-posture defaults you must rotate

**The generated compose is a turnkey *development* stack.** It boots with no
manual secret setup, which means it ships well-known default credentials. Before
any non-local deployment, rotate every item below. The relevant files are
`forge/templates/deploy/docker-compose.yml.j2`, the Gatekeeper provider compose
(`forge/features/auth/templates/platform_auth_gatekeeper/`), and the generated
`deploy/infra/gatekeeper/secrets/service_registry.yaml`.

- **Keycloak admin password** and **Postgres credentials** — shipped as
  `admin` / `postgres`. Replace with managed secrets; don't reuse the same
  Postgres password across services.
- **Gatekeeper session/delegation keys** (Fernet) and the **dev client secret**
  — regenerate; do not ship the in-tree example values.
- **Synthesized S2S secrets** in `service_registry.yaml` — deterministic in dev;
  replace with unpredictable values (the file documents the re-hash flow).
- **Signing keys** — the keygen sidecar writes an ECDSA P-256 keypair on first
  development boot. For production rotation, use the active/pending/retiring key
  lifecycle in the [deployment guide](../operations/deployment.md#signing-keys-es256-jwts); deleting a signing-key volume invalidates existing trust.
- **`ENV`** — flip from `development` to `production` to activate the
  fail-closed guards.

**Fail-closed guards (intentional).** With `ENV=production`, the realm-sync
sidecar refuses to run while the Keycloak admin password is still the `admin`
default, so a stack that still carries dev secrets will not boot into a
production posture. `in_memory` auth likewise refuses production. Treat a
crash-loop here as the guard doing its job — supply real secrets.

**Trust boundary / ports.** Datastores are bound to loopback only
(`127.0.0.1:15432→postgres`, `127.0.0.1:6379→redis`); services reach them over
the compose network (`postgres:5432`, `redis:6379`). The Gatekeeper (`:5000`),
Keycloak, and Traefik (`:80`) are host-exposed for the dev/test stack. In a real
deployment, front the stack with Traefik (or your ingress) and do not publish
the datastore or admin ports.

See [`docs/operations/deployment.md`](../operations/deployment.md) for the production checklist and
[`docs/operations/maintainer-runbook.md`](../operations/maintainer-runbook.md) for operator workflows.

## Where to go next

- [`docs/auth-architecture.md`](../auth-architecture.md) — the full auth stack
  (Keycloak IdP, Gatekeeper authority, JWT/JWKS, BFF sessions, tenant claims).
- [`docs/FEATURES.md`](../FEATURES.md) — the option registry: every knob a preset
  sets (`auth.*`, `database.*`, `infrastructure.*`) with its type and default.
- [`docs/guides/getting-started.md`](getting-started.md) — first-project walkthrough.
