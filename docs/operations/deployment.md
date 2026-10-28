# Production Deployment Guide

This guide covers deploying a forge-generated project to production.

## Trust boundaries before deployment

The generated Compose stack is a development scaffold. Its defaults publish
backend host ports, route every backend through the edge, share an application
bridge network and use the PostgreSQL administrator account for runtime access.
Changing secrets or setting `ENV=production` does not remove those access paths.

| Boundary | Production requirement |
| --- | --- |
| Public ingress | Explicitly select public APIs. Keep internal services private; remove backend host-port publishing. Protect health/admin endpoints according to their purpose. |
| Authentication | Configure ForwardAuth for public protected routes and verify signed JWTs in services. Strip spoofable identity/forwarding headers at the trusted edge. |
| Authorization | Enforce operation scopes, tenant and resource ownership at each receiver. Shared JWT audience does not isolate service recipients. |
| Transport | HTTPS at ingress; authenticated/encrypted service transport appropriate to the network threat model. |
| Data | Separate service-owned databases and least-privilege runtime roles. Use separate migration roles; runtime must not be superuser or BYPASSRLS. Apply FORCE ROW LEVEL SECURITY where owners would otherwise bypass policies. |
| Credentials | Replace deterministic development service secrets, protect signing keys, and operate rotation/revocation procedures. |
| Availability | Operate Gatekeeper and Redis as authentication dependencies; plan replication, key consistency, durable API-key records, backups and monitoring. |

Helm generation is deployment scaffolding, not proof of configured ForwardAuth,
TLS or network policies. Validate those controls in the actual ingress controller
and cluster. The generic chart does not automatically reproduce every Compose
auth infrastructure behavior.

Optional API keys require `API_KEYS_ENABLED=true` in Gatekeeper (Compose derives
it from `auth.api_keys`). Set `API_KEY_MAX_TTL_SECONDS` for the permitted lifetime.
Expose their management routes deliberately through a protected same-origin
path. Tenant quotas are generated; per-key quotas require additional policy.
Validate [S2S](../guides/service-to-service.md) and [API-key](../guides/api-keys.md)
allowed/denied journeys, including attempts to bypass ingress, before release.

OIDC verification requires the exact issuer advertised by Keycloak. The default
Compose uses `http://keycloak:8080/realms/app` inside its network; a browser on
the host reaches it only after deliberate DNS/ingress configuration.
Where browsers reach Keycloak at another URL (Kubernetes: `https://auth.<host>`),
set Keycloak's `KC_HOSTNAME` to it and Gatekeeper's `KEYCLOAK_PUBLIC_BASE_URL` to
its `/realms`. Sign-in and sign-out redirects and the expected issuer use that
URL; token, JWKS and Admin API calls keep the in-cluster `KEYCLOAK_BASE_URL`. A
tenant route's `issuer_url` under `KEYCLOAK_BASE_URL` maps the same way, any
other is used as given. Without `KC_HOSTNAME`, Keycloak names the sign-in's host
as issuer and refuses the in-cluster refresh. Do not disable issuer verification.

The generated default realm, Gatekeeper `KEYCLOAK_ADMIN_REALM`, and realm-sync
target are all `app`. Despite its name, Gatekeeper uses this setting for the
application's self-registration realm, not Keycloak's administrative `master`
realm. A custom frontend realm does not automatically update those fixed
deployment values: align Gatekeeper and realm-sync with it and supply explicit
hostname-to-tenant UUID routes for non-default hosts/realms. Validate an actual
authorization-code/PKCE login, callback, session and API request after changing
issuer or realm configuration.

## Required Environment Variables

Review the generated configuration and `.env.example` for each selected
backend/provider. The variables below describe common built-in paths; production
validation is feature-specific, so do not assume every service applies identical
secret checks.

### Runtime posture and Python application settings

| Variable | How to generate | Notes |
| --- | --- | --- |
| `ENV` | Set to `production` | Controls fail-closed behavior for secrets |
| `APP__SECURITY__SECRET_KEY` | `python -c "import secrets; print(secrets.token_hex(32))"` | Python application security setting, bound via `APP__`. Check the generated validator and selected auth mode; Node/Rust/Go have their own schemas. |

### Auth-enabled projects (Gatekeeper)

| Variable | How to generate | Notes |
| --- | --- | --- |
| `GATEKEEPER_CLIENT_SECRET` | Random string, 32+ chars | Must not be empty or `super-secret-string` |
| `COOKIE_SECURE` | `true` | Defaults to `true`; set `false` only for non-HTTPS dev envs |
| `SESSION_FERNET_KEY` | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` | Required for BFF session encryption. The per-service `gatekeeper` chart generates it when left empty and keeps it across upgrades, except when rendered without cluster access (`helm template`, Argo CD) |
| `DELEGATION_GRANT_FERNET_KEY` | Same as above | Required for delegation grants. Generated like `SESSION_FERNET_KEY` |
| `SIGNING_KEY_DIR` | Path to directory containing `.pem` files | Gatekeeper keygen init-container creates these |

### MCP-enabled projects

| Variable | How to generate | Notes |
| --- | --- | --- |
| `MCP_APPROVAL_SIGNING_KEY` | `python -c "import secrets; print(secrets.token_hex(32))"` | Required in production; service fails to start without it |

## Production Checklist

Before going live, verify:

- [ ] `ENV=production` is set on all services
- [ ] `APP__SECURITY__SECRET_KEY` is a strong random value (>= 32 chars, not a `CHANGEME…` placeholder)
- [ ] `APP__SECURITY__AUTH__CLIENT_SECRET` is the real OIDC client secret (not `changeme`) when auth is enabled
- [ ] `GATEKEEPER_CLIENT_SECRET` is set (not empty, not `super-secret-string`)
- [ ] `COOKIE_SECURE=true` is set (or omitted — defaults to `true`)
- [ ] `SESSION_FERNET_KEY` is set (if using BFF sessions)
- [ ] `MCP_APPROVAL_SIGNING_KEY` is set (if using MCP tool invocations)
- [ ] Signing key PEM files exist in `SIGNING_KEY_DIR`
- [ ] Redis is accessible via `REDIS_URL`
- [ ] Database credentials use least-privilege runtime roles and tenant-isolation tests pass
- [ ] Health check endpoints (`/api/v1/health/live`, `/api/v1/health/ready`) are monitored
- [ ] Body size limits are appropriate for your workload (Python: `audit.max_body_size`, Node/Rust/Go: 1MB default)

## Key Rotation

### Fernet keys (session + delegation grants)

1. Generate a new Fernet key
2. Update `SESSION_FERNET_KEY` / `DELEGATION_GRANT_FERNET_KEY`
3. Restart gatekeeper
4. **Impact:** All outstanding sessions are invalidated. Users must re-authenticate.

### Signing keys (ES256 JWTs)

The generated `FileKeyRing` loads fixed names: required `active.pem`, optional
`pending.pem`, and optional `retiring.pem`. It signs with the active key and
publishes the loaded public keys in JWKS. It does **not** choose the newest
arbitrarily named PEM file.

Prepare a new ECDSA P-256 private key as `pending.pem`, deploy it across issuer
replicas, and reload/restart them so clients can learn its public key. During a
coordinated switch, retain the old active key as `retiring.pem` and promote the
new key to `active.pem`; refresh every issuer consistently. Retain verification
material through outstanding-token expiry and cache propagation, then remove the
retiring key. Test the procedure against actual JWKS caches and rollout timing;
adding a file alone does not guarantee automatic reload or zero downtime.

See the [key-ring implementation](../../forge/features/auth/templates/platform_auth_gatekeeper/all/files/deploy/infra/gatekeeper/src/app/gatekeeper/key_store.py)
and [auth architecture](../auth-architecture.md) for the accepted key states.
The KMS key-ring class is a deferred stub, not a production adapter.

### Client secrets

1. Rotate in your identity provider (Keycloak)
2. Update `GATEKEEPER_CLIENT_SECRET`
3. Restart gatekeeper

## Docker Production Profile

For hardened deployments:

```yaml
services:
  api:
    read_only: true
    tmpfs:
      - /tmp
    security_opt:
      - no-new-privileges:true
    deploy:
      resources:
        limits:
          memory: 512M
```

### Non-compose deployments

If deploying with `docker run` or Kubernetes (without the compose template),
you must set `COOKIE_SECURE=false` explicitly for non-HTTPS environments.
The default is `true` — cookies will not be sent over plain HTTP.

## Kubernetes (Helm)

Generating with `deploy.target=kubernetes` emits a **topology-aware Helm
umbrella chart** under `deploy/helm/`. The chart mirrors your project: one
Deployment + Service + HorizontalPodAutoscaler per backend, the frontend, an
Ingress, and a per-backend ConfigMap + Secret. Its `values.yaml` is rendered
from the project's deployment topology, so it always reflects the real set of
backends, ports, and the frontend.

### Quick start

```bash
# Lint + render (raw manifests, derived from the chart — never hand-edited):
make helm-lint
make k8s-manifests          # writes deploy/k8s/rendered.yaml

# Install (copy the example first and fill in your overrides):
cp deploy/helm/values-prod.yaml.example deploy/helm/values-prod.yaml
make helm-install           # helm upgrade --install ... -f deploy/helm/values-prod.yaml
```

`values.yaml` is **forge-owned** — it is re-rendered (and three-way merged) on
every `forge --update`. Put your per-environment overrides in
`values-prod.yaml`, which forge **never** tracks or overwrites.

### Datastores

Postgres / Redis / Keycloak are **external by default** (the production
posture): point `externalServices.*` at your managed instances. For a throwaway
dev cluster (kind / minikube), set `infra.inCluster=true` to spin up in-cluster
stand-ins (Postgres StatefulSet + PVC, Redis, Keycloak) — these are **not**
production-grade.

### Secrets (read this before production)

forge ships **only placeholders** in each workload's `secretEnv` (DB URLs,
client secrets carry `CHANGEME`). It never bakes a real or deterministic
credential into the chart. Before going live, either:

- override `secretEnv` in `values-prod.yaml`, or
- delete the generated `Secret`s and wire an `ExternalSecret` / SealedSecret /
  CSI volume of the same name (the Deployment's `secretRef` is `optional`, so an
  externally-managed Secret takes over).

**Auth caveat:** the local stack derives gatekeeper service-to-service secrets
deterministically and stores their **argon2 hashes** in
`deploy/infra/gatekeeper/secrets/service_registry.yaml`. If you rotate the S2S
Secret in the cluster you must regenerate that registry too, or S2S auth will
reject the new credential.

### In-cluster gatekeeper (dev/demo)

For a full in-cluster auth demo (kind/minikube), set both `infra.inCluster=true`
and `infra.gatekeeper.enabled=true`. The chart then emits an in-cluster
Gatekeeper Deployment whose **keygen initContainer** mints ES256 signing keys
into a pod-shared volume before the server starts, plus a **post-install
realm-sync Job** that imports the Keycloak realm (bundled into the chart as
`deploy/helm/files/keycloak-realm.json`) via the Admin API. This is **off by
default** and **dev-only** — you must first build and push the gatekeeper image
from `deploy/infra/gatekeeper/`, and the shipped `service_registry.yaml` is a
placeholder. Production should run a managed/external Gatekeeper and Keycloak
(point `externalServices.*` at them).

### Migrations

Each backend that ships migrations gets a `Job` annotated as a
`pre-install,pre-upgrade` Helm hook, so the schema is current before the
Deployment rolls. A failed migration blocks the release (intended). All migrate
Jobs share one hook weight and therefore run **in parallel** — safe because each
backend owns its own database (so migrations never collide). If you point
multiple backends at a single shared database, give those Jobs distinct
ascending `helm.sh/hook-weight`s so Helm serialises them.

Rust and Go Jobs run `/app/migrate` from the backend image. The Go binary also
takes `status` and `version`, which only read the database and do not wait for a
running migration, and `down` (roll back the most recently applied migration);
see the service's `OPERATIONS.md`, which also covers row-level security for
`database.multitenancy=shared_rls`.

### Ingress

A standard `networking.k8s.io/v1` Ingress routes `/api/<backend>` to each
backend Service and `/` to the frontend. `ingress.className` and
`ingress.host` are values you set per environment. The default rewrite
annotation is **nginx-ingress specific** — for a different controller (Traefik
Middleware CRD, Gateway API, ...) change `ingress.className` and
`ingress.annotations` accordingly.

### Keeping the chart current

`forge --update` re-renders `deploy/helm/values.yaml` from the current
topology: add a backend, change a port, or add the frontend and the chart picks
it up. Your `values-prod.yaml` is never touched; edits to the forge-owned files
are preserved via three-way merge (or surfaced as a `.forge-merge` sidecar on a
genuine conflict).

## Monitoring

Generated projects include:

- **Health checks:** `/api/v1/health/live` (liveness) and `/api/v1/health/ready` (readiness)
- **Structured logging:** JSON format with correlation IDs
- **OpenTelemetry:** Enable with `observability.tracing=true` and set `OTEL_EXPORTER_OTLP_ENDPOINT`
- **MCP audit log:** Append-only JSONL at `MCP_AUDIT_LOG` path (default: `audit.jsonl`)

## Known Limitations

See [known-issues.md](../reference/limitations.md) for the full list. Key production-relevant items:

- PII redaction is available on Python and Go (Node/Rust backends don't filter sensitive data from logs)
- Vector store adapters are Python-only
- Admin panel has no RBAC — all authenticated users see all models
