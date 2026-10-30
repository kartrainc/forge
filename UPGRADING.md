# Upgrading forge

This document lists breaking changes per version and the migration steps for each.

## 1.1 → 1.2

### Per-service Helm charts (optional)

`deploy.helm_layout: per_service` replaces the umbrella chart with one chart per component: one per backend and
per frontend, plus `postgres`, `redis`, `keycloak` and `gatekeeper` when the project has them. The cluster needs the
CloudNativePG operator and Traefik (their CRDs). The gatekeeper chart runs `ENV=development`: any other `ENV`
refuses `SVC_AUTH_BACKEND=preshared`, the only S2S authentication the backends' clients have, so these charts are a
development setup, not production-ready, until S2S supports another backend. To switch an existing project:

1. Set `"deploy.helm_layout" = "per_service"` under `[forge.options]` in `forge.toml` and `"deploy.helm_layout" =
   "user"` under `[forge.option_origins]`, then update: the update takes options from `forge.toml`, not from the
   recipe. The new charts appear under `deploy/helm/<chart>/`. With the ownership recipe (`.forge/quality.json`) the
   umbrella's files (`deploy/helm/Chart.yaml`, `.helmignore`, `values.yaml`, `values-prod.yaml.example`,
   `templates/`, `files/`) stay on disk, no longer recorded; without it the update deletes those you did not edit,
   except `files/`. Delete what is left once nothing installs the umbrella.
2. Move environment values to each chart's `values-<env>.yaml`, which `make helm-install ENV=<env>` passes (`prod` by
   default). For `workloads.<service>`: `env` goes to that chart's `configMap.data`, `secretEnv` to
   `secret.stringData`, `replicaCount` to `deployment.replicas`, `containerPort` to `container.port`, `resources` to
   `container.resources`, `migrateCommand` to `migration.command`, `hasMigrations` to `migration.enabled`,
   `migrateSecretEnv` to `migration.ownerSecret.stringData` and `dbAppRole` to `migration.dbAppRole`. With the
   postgres chart, `migration.dbAppRoleManaged` is `true`: the chart creates the runtime role and holds its password,
   so `APP_DB_PASSWORD` goes. With an external Postgres, set it `false` and keep `APP_DB_PASSWORD`, the password in
   the service's DB URL; the owner needs `CREATEROLE`. The frontends go to their charts, `infra` to the `postgres`,
   `redis`, `keycloak` and `gatekeeper` charts, and the umbrella's Ingress to each chart's `ingressRoute`. Edits you
   made to the umbrella's templates are not carried over: repeat them in the new charts, which are scaffold too.
3. The services now reach Postgres at `postgres-rw` (CloudNativePG), as an owner role named after the database for
   migrations and, under `database.rls_app_role`, a runtime role for serving, which the postgres chart creates. Each
   role's password lives in the postgres chart's `roleSecrets.passwords` (or, with `roleSecrets.create: false`, in
   Secrets `postgres-<role>` of your own, `-` for `_`) and must match every URL that names the role: the services' DB
   URLs, the `migration.ownerSecret` URLs and the event bus's `APP__EVENTS__BUS_URL` (role `events`). Redis requires a
   password: set it in the redis chart and in every Redis URL, the gatekeeper's included. The keycloak chart's
   `admin.password` and `realm.clientSecrets.gatekeeper` must equal the gatekeeper chart's `KC_ADMIN_PASSWORD` and
   `GATEKEEPER_CLIENT_SECRET`. Data in the umbrella's in-cluster Postgres does not move by itself: dump it and restore
   it into the new cluster.
4. Uninstall the umbrella, then run `make helm-install`, which installs every chart in dependency order. With
   `infra.inCluster` the two claim the same `redis`, `keycloak` and `gatekeeper` names, and both route the same
   hosts, so they cannot run together. A Service now listens on the service's port, not 80, and a frontend's on
   8080: update anything that called `<release>-<service>:80`. Browsers reach Keycloak at `auth.<host>`, the keycloak
   chart's `hostname`: change it together with the gatekeeper chart's `KEYCLOAK_PUBLIC_BASE_URL`, that URL plus
   `/realms`.

### Tenant isolation and Keycloak strategies (unreleased)

The new safeguards are options with safe defaults. Set them in `forge.toml` to
keep a different strategy.

- **`shared_rls` projects:** services now connect as `<db_name>_app` and no
  longer migrate on start (`MIGRATE_ON_START=false`). Compose creates the role
  on `up`. In Helm, set each workload's `migrateSecretEnv` (`DB_OWNER_URL`, the
  owner DB URL, `APP_DB_PASSWORD`) and put the same password in `secretEnv`'s DB
  URL; with `infra.inCluster` the pods' init containers migrate and create the
  role, otherwise the pre-install migrate Job does. The owner needs `CREATEROLE`
  (not superuser) on Postgres 13 or newer, and `APP_DB_PASSWORD` must be
  printable ASCII. A deployment with its own non-bypassing role
  sets `database.rls_app_role=false` and points the DB URL at it; the startup
  guard refuses a superuser either way. Check with
  `SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user`.
- **Header or subdomain tenant without auth:** generation now fails. Enable
  authentication, use `token_claim`, or set `database.allow_unverified_tenant=true`
  when a trusted gateway owns the tenant header. With authentication, a request
  whose header or subdomain names another tenant than its token now gets 403
  (`shared_rls` and `schema_per_tenant`), and one with a bearer token that does
  not verify gets 401, even on a route that needs no sign-in.
- **Keycloak projects:** Keycloak moves to 26.6 and the admin variables to
  `KC_BOOTSTRAP_ADMIN_USERNAME`/`KC_BOOTSTRAP_ADMIN_PASSWORD`. The default
  `auth.keycloak_tenant_source=organization` takes the tenant from Keycloak
  Organizations. Keycloak imports the realm only into an empty database, so an
  existing realm needs organizations enabled, an organization per tenant (its id
  is the tenant id), the `organization-id` mapper on the `gatekeeper` client
  (see `deploy/infra/keycloak-realm.json`) and `post.logout.redirect.uris` set
  to the app's origin (`http://app.localhost/*` in Compose); the gatekeeper
  refuses to start until the first two are in place. Registration is
  closed unless `auth.keycloak_self_registration="own_organization"`, which
  also needs `manage-realm` for `service-account-gatekeeper`, "Verify email"
  on the realm and an SMTP server: only a verified email gets an organization.
  To keep the `tenant_id` attribute instead, set
  `auth.keycloak_tenant_source="user_attribute"`.
- **Self-registration with `user_attribute`:** registration is closed too, and
  a user without a `tenant_id` attribute is refused at sign-in instead of being
  given the default tenant (which put every self-registered user in the same
  tenant). For a single-tenant application that wants open sign-up, set
  `auth.keycloak_self_registration="default_tenant"`. An existing realm keeps
  `registrationAllowed` until changed in Keycloak.
- **Organization switch:** `/auth/organization` is now a form `POST` (`alias`,
  optional `redirect_uri`) from the app's own origin; a `GET` link no longer
  switches, and `/auth/login` ignores an `organization` parameter.
- **Gatekeeper client origins:** the realm's `gatekeeper` client accepts
  `redirectUris`, `webOrigins` and `post.logout.redirect.uris` only for the
  apps' origins instead of `*`/`+`. Helm fills them from `ingress.host` and
  `frontends.<name>.host` when Keycloak first imports the realm; Keycloak keeps
  an existing realm, so after changing those hosts update the client's URIs in
  the admin console (or the realm export) too.
- **Subdomain tenants with authentication:** a subdomain or header tenant that
  is not a UUID must be the token's `https://forge/tenant_slug` (any case),
  else 403, in Go too (which ignored such subdomains). Without a verified token
  a slug binds no tenant.
- **Realm per subdomain:** without a TMS `tenant-route`, the gatekeeper now signs
  users in to the project's realm. Set `REALM_PER_HOST=true` on the gatekeeper to
  keep deriving the realm from the host.

### Coordinated dependency refresh (unreleased)

Regenerate a candidate and review its ownership-aware update before adopting
these template migrations. Keep application-specific behavior in custom modules,
then run architecture and all three native test suites against the candidate.

- **Node services:** Prisma 7 uses `prisma.config.ts` for CLI connection settings
  and `@prisma/adapter-pg` at runtime. Include the config in deployment images.
  Keep `DATABASE_URL` available to migration commands. The adapter preserves
  `schema`, converts `connection_limit` to PostgreSQL pool `max`, and converts
  `pool_timeout` seconds to `connectionTimeoutMillis`, including zero to disable
  the timeout. These URL settings work without enabling the optional pool feature;
  that feature adds environment-based defaults. Zod 4 uses `.issues` and
  `.prefault()` where nested defaults must still be parsed. Vitest moves to 5.
- **Authentication SDKs:** Node uses JOSE 6's Web Crypto keys (`CryptoKey`).
  Rust uses jsonwebtoken 11 with the explicit AWS-LC backend and rejects malformed
  optional `nbf` claims. Run the shared parity fixtures when changing token or
  authorization handling; install the `auth-parity` development group to exercise
  the Python runner and build the Node SDK before testing it.
- **Rust services:** SQLx 0.9 raises the generated workspace minimum to Rust 1.94.
  Dynamic repository queries use `QueryBuilder` with bound values. reqwest 0.13
  uses the `rustls` feature name; update custom HTTP integrations accordingly.
  Keep Rust Docker build and runtime stages on the same Debian release; the
  template pins both to Bookworm so newer libc symbols cannot break startup.
- **Optional adapters:** OpenTelemetry uses its current resource/provider builders
  (Node 2 and Rust 0.33). Rust HTTP export uses the blocking client required by
  the default batch processor. Queue/cache dependencies move to BullMQ 6,
  ioredis 6, Node Redis 6, Rust Redis 1.7 and Apalis 0.7. AI SDK 7 streams
  `tool-input-*` events; the adapter preserves Chat Completions routing and
  assembles each tool's JSON arguments once. Rust's async-openai 0.42 requires
  explicit chat-completion/embedding features and namespaced request types.
- **Flutter:** generated apps and the canvas widget package require Flutter 3.47
  and Dart 3.13. Material widgets now come from `material_ui`; the Markdown
  renderer uses the official compatibility bridge while its dependency still
  relies on Flutter's legacy Material theme and localizations. Replace
  `flutter_markdown` imports with `flutter_markdown_plus`, resolve vendored
  packages separately, and regenerate Freezed/Riverpod/Retrofit output.
  Riverpod lint runs through Dart's native analyzer plugin configuration rather
  than the removed `custom_lint` dependency.
- **TypeScript:** retain stock TypeScript 6.0.3 until the Vue tooling supports
  stock TypeScript 7. The compatibility failure is tracked in
  [issue #357](https://github.com/cchifor/forge/issues/357); a compiler fork is not
  substituted automatically.
- **Web frontends:** use Node 26 or newer (see "Node 26" below). Vite 8 uses Rolldown build options.
  SvelteKit 3 puts adapter/preprocessor settings in the Vite plugin and replaces
  `$app/stores` with `$app/state`. Resolve application paths through
  `$app/paths.resolve()` so navigation respects the deployment base; runtime
  redirect destinations must stay within the application. Query 6 uses reactive
  option functions.
  Ky 2 hooks receive a state object and use `prefix` rather than `prefixUrl`.
  Regenerate API clients with OpenAPI TS 0.99 and mock workers with MSW 3.
  OpenAPI generation is now explicit: `npm run codegen` writes service-specific
  types (Svelte) or a client SDK (Vue) to `src/custom/api/`. Import them from
  application code. Starting the dev server no longer regenerates clients or
  overwrites Forge-owned generic API/feature types. Update custom OpenAPI
  configuration and imports to use this extension directory.
  Lucide imports move to `@lucide/vue` and `@lucide/svelte`. Browser coverage uses
  the shared `scripts/browser-coverage.ts` plugin and the compiler's actual
  source maps. Keep this file when migrating custom Vite configurations.
  TanStack Table 9 uses explicit features and reactive state atoms; the generated
  DataTable preserves existing column preferences and maps public `left`/`right`
  pinning positions to the new internal `start`/`end` values.

Follow the [coordinated upgrade checklist](docs/operations/maintainer-runbook.md#coordinated-dependency-upgrades)
for locked installs, generated applications, release artifacts and CI sign-off.

### Python SDK migration from 1.1 and 1.2 alphas

The early 1.2 alpha replaced the Python `src/service/` shim with `weld-*`
imports. Its [original migration notes](docs/archive/1.2-alpha-weld-migration.md)
preserve the import table, copier prompts, optional features, deployment and Vue
changes for projects built with that version. That SDK layout has since been
superseded: current Python services ship their own `sdks/forge-core` and use
`forge_core` imports. Generated authentication additionally supplies `platform_auth`.
Neither regeneration nor `sdk_consumption=none` preserves the old shim.

For custom code migrating to current Forge, review these public equivalents:

| Previous import family | Current application dependency |
| --- | --- |
| `service.db`, `service.repository`, `service.uow`; `weld.core.persistence` | `forge_core.persistence` (`AsyncDatabase`, `AsyncBaseRepository`, `AsyncUnitOfWork`, mixins) |
| `service.security`; `weld.fastapi.security` | `forge_core.security`; use the optional `platform_auth` public API for its delegation/S2S contracts |
| `service.core.context`, `service.domain`; `weld.core.context`, `weld.core.domain` | `forge_core.domain` and `forge_core.domain.context` |
| `service.discovery`; `weld.core.discovery` | `forge_core.discovery` |
| `service.utils.fastapiutils`; `weld.fastapi.api.errors` | `forge_core.errors` and the application's error port/handlers |
| `service.api`; `weld.fastapi.api` filtering/pagination | `forge_core.api` |
| `service.observability`; `weld.observability` | `forge_core.observability` |
| `service.client`; `weld.http_client` | Explicit application HTTP client; `platform_auth.S2SClient` for authenticated service calls |
| `service.tasks` | Select a supported queue option and implement the application's job contract |

These are API families, not a mechanical namespace substitution: compare public
signatures, error envelopes, transactions and identity handling in the rendered
candidate. Preserve required external SDK dependencies until their consumers are
migrated. `weld_base_sdks` is no longer a Python copier prompt; `sdk_consumption`
now controls the sibling Docker SDK build context, while the service-local
`forge-core` is always shipped. `service_path_prefix` still controls routing.
Resolve optional features against the current [catalog](docs/FEATURES.md); the
archived defaults and proposed `auth.mode=weld` are not current promises.

For Vue, preserve custom UI behavior and migrate generated API-client imports to
`src/custom/api/` as described above. `consumed_services` selects service-specific
OpenAPI output; run code generation explicitly. Review the candidate's lockfiles,
Docker build contexts, migrations and entrypoints, then run architecture, unit,
integration and E2E gates before replacing the old deployment.

### Direct routing defaults

The `service-proxy` and legacy `api-gateway` application templates have been
removed. Existing configurations selecting either name fail validation; Forge
does not silently replace them or delete an existing generated service.

For an existing project, preserve any custom application behavior, then create a
reviewable candidate with an explicit backend list containing the domain services.
Update client URLs to their edge routes, and implement necessary business calls
using the S2S SDK. Remove references to the retired service from dependencies,
deployment and the recorded generation recipe as part of the reviewed topology
migration. Validate user identity, scopes and tenant access before retiring the
old deployment. Do not rename the template and assume authorization is unchanged.

API keys now require `auth.api_keys=true` (`API_KEYS_ENABLED=true` in Gatekeeper;
generated Compose sets it). Existing unscoped or non-expiring keys are rejected
and must be reissued with explicit scopes and an expiry. Creating administrators
must hold the requested scopes as well as the admin role. Sessions now bind the
verified tenant ID; older hostname-based sessions are invalidated and their
cookies cleared. API/session requests return 401 to trigger reauthentication;
page navigations begin a fresh login.
Non-default realms also need explicit hostname-to-tenant UUID routing records
(normally provisioned by TMS). Only the configured default realm falls back to
`DEFAULT_TENANT_ID`; signing in again does not create missing tenant routes.

User-delegation grants now retain their scope ceiling and bind to their issuing
client and target. Older grants lacking these fields must be reissued. API-key
and service-account identities cannot be used as user-delegation subjects.
Replace/drain every older Gatekeeper instance during rollout: upgraded instances
reject legacy grant exchange and cannot establish ownership for API revocation.
Legacy Redis records expire within their original TTL (at most 24 hours); an
operator can instead purge those `gk:delegation_grant:*` records during a coordinated
cutover before issuing replacements. Grant lookup does not extend their TTL.
See [S2S](docs/guides/service-to-service.md) and [API keys](docs/guides/api-keys.md).

### Frontend fragment files move into the frontend app

Fragments that target a frontend now write into `apps/frontend/`. Earlier
versions wrote the session-timeout composable and dialog (Vue, Svelte and
Flutter) and the MCP UI components to a root `src/` or `lib/`, outside any app,
and placed a `.keep` file from the Gatekeeper key-generation fragment at the
project root. A project generated with Gatekeeper authentication and a Vue
frontend contained `src/shared/composables/useSessionTimeout.ts` and
`src/features/auth/components/SessionTimeoutModal.vue`.

`forge --update` writes these files inside the app, deletes the protected root
copies and removes their records from `forge.toml`. It leaves editable files the
generator no longer writes in place, here the root `.keep` and Flutter's root
`test/src/features/auth/data/session_timeout_service_test.dart`: delete them,
then the root `src/` or `lib/` once nothing else uses it. A project without the
ownership recipe (`.forge/quality.json`) keeps the root copies and their records
until it adopts the recipe, which `forge --quality migrate` proposes.

Nothing mounts the session-timeout dialog automatically. To show it, mount
`SessionTimeoutModal`, or your own dialog built on `useSessionTimeout()`, once in
the authenticated layout. The Flutter `SessionTimeoutModal` needs an `onSignOut`
callback outside the web; on the web it defaults to navigating to `/logout`.
Flutter apps now declare `http` and `url_launcher` even without the login UI
(`include_auth=false`), because the session-timeout fragment imports both.
An ownership-managed update stops, without changing anything, when `forge.toml`
records the frontend app anywhere other than `apps/frontend`, where the
generator writes it. The MCP UI components are written only to Vue apps;
`mcp.config.example.json` stays at the project root. Plugin fragments that declare
`target_frontends` now land in the app as well, so ship their `files/` tree
app-relative (`files/src/...`).

### Vue UI kit option (unreleased)

`frontend.ui_kit` (`--ui-kit`) selects the Vue template's component library:
`shadcn` (the default) or `nuxt-ui` ([ADR-011](docs/architecture-decisions/ADR-011-ui-kit-option-for-the-vue-template.md)).
Existing projects keep their output. On the next `forge --update` the quality
recipe gains `ui_kit` and `forge.toml` records it under `[forge.frontend]`;
older recipes and manifests read as `shadcn`. The option applies to Vue only:
setting another kit for Svelte or Flutter is refused.

The generated `scripts/post_generate.py` no longer carries the no-chat
`App.vue` and `MainLayout.vue` or the no-Keycloak `useAuth.ts` as strings.
The template renders them under Copier conditional file names, with the same
bytes as before.

Plugin authors: a component feature declares the kits it renders with in
`[feature.component].ui_kits` (default `["shadcn"]`) and may name a
replacement per kit in `[feature.component.replaced_by]`; a fragment may set
`Fragment.ui_kits` (default: any kit). Forge refuses, at plan time, a component
or fragment whose kits exclude the project's. Plugin layouts render with
`shadcn` unless they pass `ui_kits` to `add_frontend_layout` (SDK 1.6). A
fragment may ship a `files-<kit>/` tree beside `files/`; for a project on that
kit its files replace the same paths of `files/`.

#### Projects already on Nuxt UI

A project generated on `nuxt-ui` before the agent tooling existed gains
`.agents/skills/nuxt-ui/`, `.claude/skills/nuxt-ui/`, `.mcp.json` and
`.codex/config.toml` on its next `forge --update`. If any of them already
exists without a forge record, for example your own `.mcp.json`, the update
stops on the collision before changing anything: move it aside, update, then
merge your entries back into forge's file. Forge ships these files whole and
never merges into a file it did not write.

#### Switching a project to Nuxt UI

Set `ui_kit` to `nuxt-ui` under `config.frontend` in `.forge/quality.json`
(and in your stack file, if you keep one) and run `forge --update`. Nuxt UI
takes the `sidebar` layout and no AI chat; the update refuses other settings
before changing anything.

- Generated files are rewritten. `src/shared/ui/`, the settings store and
  `useTheme.ts` leave the provenance and are deleted.
- Untouched scaffold is updated in place, the screens and CRUD pages included.
  Edited scaffold receives `.forge-merge` sidecars, typically `package.json`,
  `vite.config.ts`, `index.html`, `src/app/main.ts`, `src/app/App.vue`,
  `src/app/styles/main.css` and the tsconfig files.
- Scaffold the kit no longer emits stays on disk: delete `components.json`,
  `tailwind.config.ts` and `tests/integration/button.test.ts`,
  `primitives.test.ts` and `splitter.test.ts`. The three suites import the
  deleted kit and fail until removed.
- The brand module lands in `src/custom/theme/`: `nuxt-ui.config.ts` (the
  palettes, primary from `default_color_scheme`) and `tokens.css`. It is
  scaffold: a later starter change updates an untouched file in place and
  arrives as a sidecar for one you edited.
- The stored colour mode moves from the `theme` key to VueUse's
  `vueuse-color-scheme`; `system` becomes `auto`. Users see their system mode
  once until they choose again.
- The project gains the Nuxt UI agent tooling: `.agents/skills/nuxt-ui/`,
  `.claude/skills/nuxt-ui/`, `.mcp.json` and `.codex/config.toml`
  ([agentic usage](docs/guides/agentic-usage.md#nuxt-ui-projects)). If any of
  them already exists without a forge record, the update stops on the
  collision before changing anything: move it aside, update, then merge your
  version back.

#### Switching back to shadcn

Set `ui_kit` back to `shadcn` the same way. The shadcn kit is regenerated and
untouched screens are rewritten. The scaffold only Nuxt UI emits is taken back
where you never changed it; a file you changed stays, keeps its forge record and
is listed as left in place (ADR-015):

- `src/app/providers/head.ts` and `head.test.ts`, and `src/custom/theme/`.
  They import `@unhead/vue` and `@nuxt/ui`, which shadcn projects do not
  depend on: move what you need out of an edited copy and delete it, or the
  type-check and the unit suite fail.
- The agent tooling: `.agents/skills/nuxt-ui/`, `.claude/skills/nuxt-ui/`,
  `.mcp.json` and `.codex/config.toml`. An edited copy stays recorded, so a
  later switch to Nuxt UI keeps it (or proposes forge's newer version beside it
  as a `.forge-merge` sidecar) instead of stopping on a collision.

### Go backend (unreleased)

Go is now a built-in backend (`language: go`). These changes can affect an
existing installation:

- **Parity tier 1 covers Go.** A fragment that declares or derives tier 1 from
  Python, Node and Rust implementations alone still loads as tier 1, with a
  `DeprecationWarning`. Before 1.3, add a `BackendLanguage.GO` implementation or
  declare `parity_tier=2`; forge's own tier-1 fragments already cover Go.
- **The `forge-go-backend` example plugin registers `go-stdlib`.** A project
  generated with that plugin before this release recorded `language = "go"`, which
  now resolves to the built-in Go template. Before running `forge --update`, change
  that backend's language to `go-stdlib` in `forge.toml` and in the recipe
  (`.forge/quality.json`), with the plugin installed, so the update keeps rendering
  the stdlib service.
- **Go services enable authentication in production only with platform auth.**
  The production profile sets `security.auth.enabled` from the generated auth
  mode, so an `auth.mode=none` service starts without `GATEKEEPER_ISSUER` and
  `SERVICE_AUDIENCE`. Python, Node and Rust profiles are unchanged.
- **Go migrations below `00100` belong to forge.** `migrate` applies
  out-of-order migrations, so a forge update can add one numbered below the
  project's latest. Number project migrations from `00100`; one numbered
  `00002`–`00099` can collide with a later forge migration of the same version.
- **The items repository runs through a scope.** `internal/app/app.go` gains a
  `// FORGE:REPOSITORY_SCOPE` marker after `NewPgItemRepository`, and
  `PgItemRepository` gains `WithScope`, which `database.multitenancy=shared_rls`
  uses to bind `app.current_tenant` per transaction. A project that edited either
  file gets a `.forge-merge` sidecar on `forge --update`; keep the marker when
  resolving it.
- **`forge --update` needs `go` for Go services.** Updating or planning an
  update now runs `go mod tidy` on the regenerated services and stops before
  writing anything when `go` is not on PATH. If an earlier update broke a module
  (`go: updates to go.mod needed`), the next update repairs it: an untouched
  `go.mod` is replaced by the tidied one and `go.sum` is recorded again; a
  `go.mod` you already tidied is kept without a `.forge-merge` proposal.
- **Keep applied Go migrations.** Never delete or renumber an applied
  migration: `migrate down` refuses while an applied version has no file.
  `migrations.Open` returns `*migrations.Migrator`, which embeds
  `*goose.Provider`; code that stored the result in a `*goose.Provider` variable
  needs `.Provider`.
- **Go `shared_rls` covers forge's `items` only.** Add `ENABLE`/`FORCE ROW LEVEL
  SECURITY` and a `tenant_isolation_<table>` policy for project tables in a
  migration numbered `00100` or higher, and run that table's repository through
  the tenant scope (see the service's `OPERATIONS.md`). Data migrations must bind
  `app.current_tenant` per tenant or run as a BYPASSRLS role. Right after
  enabling `shared_rls` on an existing database, `migrate down` rolls back
  `00099` first. Remove any `TENANT_RLS_GUC` override: other values stop boot.
- **Go `shared_rls` without platform auth now honours the request tenant.** With
  `auth.mode=none`, a tenant header or `<uuid>.` host makes the request act as
  that tenant; rows created earlier under the dev tenant
  `00000000-0000-0000-0000-000000000001` stay visible only to requests that name
  no tenant, and a non-UUID header value gets 400.
- **Go logs are redacted in every binary.** With `middleware.pii_redaction`
  (on by default), every logger from `app.Logger` redacts; text-handler output
  shows maps and structs as quoted JSON and `[]byte` attributes as strings
  rather than base64. `InstallPIIRedaction` is a no-op when the default logger
  already redacts.
- **Go response cache keys default to `<service>:cache`.** Set
  `RESPONSE_CACHE_PREFIX=forge:cache` to keep existing entries, or let them
  expire.

### Executable Postgres init script (unreleased)

`deploy/compose/init-db.sh`, which creates each backend's database and
Keycloak's, is now generated with mode 0755. On macOS with Docker Desktop the
0644 file failed with `bad interpreter: Permission denied`, so `docker compose
up` created only `POSTGRES_DB`.

- An untouched `init-db.sh` gets the mode on the next update. If you edited it,
  run `chmod +x deploy/compose/init-db.sh` yourself.
- Postgres runs init scripts only on an empty data directory, and a failed run
  still leaves it initialized. If your stack never created its databases,
  delete the Postgres volume once after updating, for example with
  `docker compose down -v`, which also deletes the other volumes.

### Frontend dev server (unreleased)

`frontend.dev_server` (Vue only, off by default) runs the Compose frontend as a
Vite dev server with hot reload; see the
[guide](docs/guides/frontend-dev-server.md). With the option off, an existing
project changes only by recording `"dev_server": false` in `.forge/quality.json`
(and, for pnpm, the Dockerfile change below); the Compose file and `forge.toml`
stay as they were.

An update reads the recipe, `.forge/quality.json`, and nothing else: `forge --update
--dev-server` has no effect. Set `"dev_server": true` in the recipe's `config.frontend`
(the primary app; `config.frontends[0]` is a mirror that forge rewrites) and run
`forge --update`. It:

- adds `docker-compose.dev.yaml` and `apps/<frontend>/vite.dev.config.ts`, and
  a `dev` stage in `apps/<frontend>/Dockerfile` (`deps`, `source`, `dev`, then
  `builder` and the nginx stage from `source`);
- stops with `New generator output collides with user file` if your project
  already has a `docker-compose.dev.yaml` or `vite.dev.config.ts`: move yours
  aside, update, then fold your changes in;
- proposes the Dockerfile change as `apps/<frontend>/Dockerfile.forge-merge` if
  you edited the Dockerfile. Put any `COPY --from=<named context>` lines of your
  own in the `source` stage, so the dev image holds them too, and resolve with
  `forge --quality resolve`;
- adds a section to `apps/<frontend>/README.md`, includes the wrapper in
  `apps/<frontend>/tsconfig.node.json`, and lets the pre-commit `check-yaml` hook read
  the overlay's `!reset` and `!override` tags (`args: [--unsafe]` in
  `.pre-commit-config.yaml`). A file of these you edited gets a `.forge-merge` sidecar.
  While any sidecar is pending the update writes only sidecars: nothing else lands, no
  overlay, wrapper or `dev` stage, until you resolve them and run the update again.

Turning it off removes the two new files when you have not changed them. A file you
changed stays on disk, is printed by the update as `left in place` (and listed as
`left_in_place` in the JSON report), and stays recorded as forge's, so turning the option
on again does not collide with it. Delete such an overlay yourself: the Dockerfile no
longer has the `dev` stage it builds, so `up` with it fails with
`target stage "dev" could not be found`.

A pnpm project's Dockerfile now installs pnpm 10 instead of `latest`, with or without this option: the newest major refused a lockfile the host's pnpm had just written, so the frozen install failed. An unedited Dockerfile updates on its own; an edited one gets a `.forge-merge` sidecar.

A newly generated pnpm project also records the host's pnpm in `package.json` (`"packageManager": "pnpm@<version>"`), and corepack installs exactly that version in the image, so the image and the lockfile agree whichever major the host has. The Dockerfile's `pnpm@10` is only the default for a project without the field. An existing project is not changed: add the field yourself (`corepack use pnpm@<your version>`) to take the same protection.

Needs Docker Compose 2.39.4 or newer; `forge doctor` checks it. `forge` no longer
runs `docker compose up` for a project with the option on, because that would
start nginx: it prints the command that starts the dev stack.

### Frontend image build context (unreleased)

The frontend image is built from the whole `apps/<frontend>` directory, and
its `COPY . .` merges that directory file by file over the image's own
install. A host `node_modules`, build output and `.env.*.local` files were
sent with every `docker compose build`, and a local `.env.production.local`
was inlined into the served bundle. Every frontend now ships
`apps/<frontend>/.dockerignore`.

- A project without the file gets it on the next update.
- A project that wrote its own `apps/<frontend>/.dockerignore` makes the
  update stop with `New generator output collides with user file`. Move your
  file aside, run the update, then fold your own patterns into the generated
  file: it is scaffold, so later updates keep your edit or propose a
  `.forge-merge` sidecar.

### Browser suites keep the project's authentication setting (unreleased)

`forge --quality test` and the rendered lanes used to run the Vue and Svelte
browser suites with `VITE_AUTH_DISABLED=true` forced, whatever the project's
`.env` said. They now run the app as the project configures it, as the unit
and integration suites always did; so does the content-security check.

- Nothing serves `/auth/*` in the browser suites, so a suite of a project with
  authentication on answers the session in the page. The template's specs call
  `mockSession(page)` from the new `tests/e2e/session.ts`, which answers
  `/auth/userinfo` with the development user's details; a spec that routes
  `/auth/*` itself after it takes precedence.
- Untouched `application.spec.ts` and `crud.ts` are updated in place; edited
  ones receive a sidecar. A spec of your own that reached a signed-in page
  only because authentication was forced off calls `mockSession` too.
- A project whose specs already mock the session, signed in or out, runs them
  as written again: forcing authentication off had made the app skip those
  mocks.

### Node HTTP service (unreleased)

Node backends can take the `http-service` app template
(`app_template: http-service`, [ADR-014](docs/architecture-decisions/ADR-014-a-node-http-service-without-a-database.md)):
the Node template without a database, its migrate step, the sample `items`
entity, or the queue and cache fragments. Every Node service also gains a
`.dockerignore`, more tests and three fixes.

- Each Node service gains a `.dockerignore` and
  `tests/integration/setup-env.ts`.
- The E2E suite gains `tests/e2e/entrypoint.test.ts`, `tests/e2e/network.test.ts`
  checks the verifier when platform auth is on, `vitest.e2e.config.ts` loads
  `tests/integration/setup-env.ts`, and the error-handler suite gains a case.
- An untouched file gets the change on the next update. A
  `vitest.integration.config.ts`, `vitest.e2e.config.ts` or test you edited
  gets a `.forge-merge` sidecar: fold the new setup file or case into yours.
- The circuit breaker now adds `@types/opossum` to `dependencies`. If you added
  it yourself under `devDependencies`, keep one of the two.
- Each service's `docker-compose.yaml` and `docker-compose.fragment.yaml` set
  `APP__SERVER__PORT` in place of `PORT`, which the config never read. One you
  edited gets a `.forge-merge` sidecar.

### Frontend content-security policy (unreleased)

`security.csp` (on by default) used to emit `deploy/infra/nginx-csp.conf`,
which nothing included. It now protects the Vue frontend itself, whichever UI
kit it uses, unless the project has the AI chat:

- `apps/<frontend>/nginx.conf` and the Helm chart's default server block send
  X-Content-Type-Options, X-Frame-Options, Referrer-Policy and
  Permissions-Policy with every response, and with `index.html` a
  Content-Security-Policy without `'unsafe-inline'` or `'unsafe-eval'` whose
  nonce is nginx's `$request_id`. `index.html` is served `no-store`.
  Strict-Transport-Security is left to your TLS terminator.
- An untouched `nginx.conf` is updated in place; an edited one receives a
  sidecar. `deploy/infra/nginx-csp.conf` is scaffold and stays on disk: delete
  it. A project that serves its own policy keeps it and adopts the nonce by
  merging the sidecar.
- The build writes a nonce placeholder on its tags (`html.cspNonce` in
  `vite.config.ts`). Every Vue project, the chat ones included, also changes
  where the policy would need it: `index.html` loses its inline `style`
  attribute, whose font family moves to `main.css`; the app reads the nonce,
  when there is one, for the `<style>` elements its kit inserts
  (`src/app/providers/csp.ts`, imported first by `main.ts`); zod parses
  without `eval`; the e2e specs collect coverage only when
  `FORGE_BROWSER_COVERAGE` is set.
- Code of your own that inserts `<style>` elements or writes `style`
  attributes as markup is blocked from now on; Vue's `:style` bindings are not.
  Add the origins your frontend calls, such as an error tracker or a
  WebSocket server, to `connect-src` in `nginx.conf`: the policy admits the
  page's own origin and an external API's, not `ws:` or `wss:` at large, which
  would admit every host. To serve without the policy, set
  `"security.csp" = false` under `[forge.options]` in `forge.toml`, and
  `"security.csp" = "user"` under `[forge.option_origins]`, then update: the
  update takes options from `forge.toml`, not from the recipe.
- `scripts/serve-csp.mjs` and `playwright.csp.config.ts` run the browser suites
  against the production build under the policy, with the project's
  authentication setting: `npm run build && npx playwright test -c playwright.csp.config.ts`.
  Nothing serves `/auth/*` there, so the suites answer the session in the page
  (above). The policy is read from `nginx.conf`; a project that keeps its own
  policy in a header template names that file in `csp-check.json` beside the
  config, with the values of its `${NAME}` variables
  (`{"policy": "security-headers.conf", "variables": {"MEDIA_ORIGINS": "…"}}`);
  exactly one source names the policy, and both or neither, an undeclared
  variable or two different policies stop the check. Before the suites, the
  script opens a canary page of its own in the project's Playwright Chromium,
  under a fixed policy that blocks the page's script whatever the project's
  policy admits; the check fails without its report, so a collector that
  receives nothing cannot pass. The app's pages are served untouched. Both
  scripts are scaffold: an update replaces them while you have not edited
  them and writes a `.forge-merge` sidecar once you have (an update without a
  recipe writes them only when absent). Give the config the specs, projects
  and builds a production run can serve (a development-only route or a second
  build is the project's to arrange). A serve script older than the canary
  makes the check warn, not fail, until you take the current one. The current
  script answers a body that is no report, and a path with a malformed
  percent-escape, with 400 and keeps serving; it records such a body as
  unreadable, which fails the check.
- A project that keeps its own policy and adopts the nonce rewrites the
  placeholder in `index.html` wherever the page is served: in `nginx.conf` and
  in the Helm chart's server block, or every script is blocked.
- Svelte, Flutter and plugin frontends, and Vue projects with the AI chat, are
  served as before, without a policy. The chat's MCP panels load remote
  origins in frames, which the policy would block.

### Several frontends per project (unreleased)

A project can list several frontends under `frontends:`
([ADR-012](docs/architecture-decisions/ADR-012-a-list-of-frontends.md)). A
project with one frontend keeps its apps, services and routes. On the next
`forge --update` the recipe gains `frontends`, `forge.toml` is re-stamped to
v5, the generated gatekeeper's `config.py` and `tenant_config.py` gain the
host-label setting, and a Kubernetes chart gains two pieces that render
nothing with one frontend.

- **`forge.toml` moves to schema v5.** A v4 manifest reads as a list of one
  frontend and is re-stamped to v5, unchanged apart from the version number.
  With two or more frontends a `[[forge.frontends]]` array lists them, the
  primary first; `[forge.frontend]` keeps the primary. Tools that read
  `[forge.frontend]` keep working; tools that pin `schema_version = 4` must
  accept 5. An older forge refuses a recipe written now ("Unsupported quality
  recipe version"); on a project without a recipe it reads a v5
  manifest but sees only the primary, re-renders only that app and re-stamps
  the manifest as v4 without the array. Run one forge version on a project.
- **The recipe moves to version 2.** `.forge/quality.json` lists every app under
  `frontends`, beside `frontend`, and records each frontend's `name`. Edit the
  primary under `config.frontend`, as before (its copy in `frontends` follows on
  the next update), and further apps under `config.frontends`. A version 1
  recipe reads as one frontend, and its `features: []` reads as "every backend
  feature", the meaning it had; the next update writes version 2.
- **`features` on a frontend is now read.** In a stack file, an absent key
  means every backend feature, `[]` no CRUD pages and a list those pages; each
  listed page must be a backend feature or the file is refused. The builder
  used to drop the key, so a `frontend:` table that already set it now takes it
  into account: check such a stack file before updating.
- **`name` exists on `frontends:` entries only.** It sets the app's folder
  (`apps/<name>`). The single `frontend:` table ignores it, as before, and keeps
  the app at `apps/frontend`.
- **A `frontends:` list reads like a `backends:` list.** Its entries give every
  value, so frontend CLI flags beside it have no effect, as backend flags have
  none beside `backends:`; a `frontends:` value that is not a list falls back to
  the single `frontend:` table, as `backends:` falls back to `backend:`.
- **Every Kubernetes project's chart gains two inert pieces.**
  `templates/frontends.yaml` renders the further apps from a `frontends:` values
  map, and the gatekeeper container takes `GATEKEEPER_FRONTEND_HOST_LABELS` from
  their hosts; with one frontend the map is absent and both render nothing.
- **The generated gatekeeper reads `GATEKEEPER_FRONTEND_HOST_LABELS`.** Empty
  (the default) keeps its behaviour. A gatekeeper you run yourself in front of
  several apps needs the first label of each further app's host listed there.
- **Python API (internal).** `ProjectConfig` is not part of the plugin API
  (`forge/api.py`), which SemVer covers, and forge's CLI and stack files do not
  depend on it. Code that builds one directly passes `frontends=[...]`, as it
  passes `backends=[...]`; `frontend=` is not accepted. `config.frontend` still
  returns the primary and can still be assigned. `FrontendConfig.features` defaults to `None`
  (every backend feature), and an app's pages are
  `fc.page_features(config.all_features)`.
- **Template updates reach every backend.** A template bump now re-renders each
  backend of that language, not only the first one.

### Node 26 (unreleased)

Node 26 is the default runtime. On the next update:

- **Frontend apps** build on `node:26-slim` (their Dockerfile is regenerated) and
  their `package.json` asks for Node 26 in `engines` and takes the current
  libraries; an edited `package.json` gets the new version as a `.forge-merge`
  proposal. Run the frontend locally on Node 26.
- **Node services** keep the `node_version` their recipe records: an existing
  project recorded `22` and stays on it until you change it. A new stack file
  without `node_version` gets `26`. `@types/node` now follows `node_version`
  (`^<major>.0.0`), and the template's libraries take their latest patches.
  The choices are `26`, `24` and `22` (forge's prompt, `--node-version` and the
  template's Copier prompt): the template's toolchain (vitest 5) does not run on
  Node 20 or 18. A recipe that records one of them keeps it; move it to `22` or
  newer.
- **Python services** are offered `3.13` and `3.14`, the versions the template's
  Copier prompt takes; forge used to offer `3.12` and `3.11`, which Copier refused,
  so no project records them.
- **The workspace root's** `package.json` asks in `engines` for the oldest Node a
  Node service records (`>=22.18.0` for `22`), Node 26 without one: each service's
  image runs `npm ci` at the root on its own Node.
- **The generated quality workflow and the e2e harness** run on Node 26.

### Frontend application templates (unreleased)

A project that sets nothing renders every file it rendered before, except the
recipe, which records the new frontend keys with their defaults
([ADR-015](docs/architecture-decisions/ADR-015-frontend-application-templates-layouts-and-menus.md)).
Three changes reach existing projects on their next update:

- **Discontinued scaffold files are taken back.** A scaffold file forge no
  longer emits (another UI kit, a page dropped from `features`, the sample
  application left for `app-shell`) is deleted when its record says forge wrote it
  as scaffold and it is unchanged; any other (edited, or recorded incompletely) is
  listed as "left in place" and keeps its record. The update prints each file it
  deletes as `taken back` (`taken_back` in the JSON report): code of yours that
  imports one no longer builds. `forge --plan-update` lists them before anything is
  written. A pending `.forge-merge` beside such a file stops the update until it is
  resolved. Before, only generated files and the dev-server overlay were handled;
  the rest stayed on disk unrecorded.
- **Chat-less Vue apps lose three orphan files**: `src/features/ai_chat/ui_protocol.gen.ts`,
  `src/features/ai_chat/events.gen.ts` and `src/core/ui-protocol/types.ts`. Nothing in
  the template imports them without the chat.
- **Reserved page names** only refuse a backend feature a frontend renders as a
  page.

#### From `crud-app` to `app-shell`

1. In the stack file and in the recipe's `config.frontend` (the primary's record;
   `frontends[0]` mirrors it), set `app_template: app-shell`, `features: []`,
   `layouts`, `routing.landing` (and any project destinations), `auth` and
   `extras`.
2. Run `forge --update`. It deletes the sample pages, tests and layout files the
   project never edited and lists the edited ones as left in place.
3. Move what the project still needs out of the files left in place, delete
   them, and compose the project's routes, layout wrappers, menu actions and
   authentication contract in `src/app/router/index.ts` and `src/app/auth.ts`.
4. Run the gates: architecture, the unit, integration and e2e suites, coverage
   and the content-security check. The generated tests of the application
   runtime count toward the integration and e2e tiers.

## 1.0 → 1.1

The 1.1 series opens the 12-month post-1.0 roadmap. Early alphas are additive except where called out below.

### 1.1.0-alpha.1 — structured error hierarchy (Epic D)

`GeneratorError` is no longer a distinct class. It is now an **alias** for the new `ForgeError` base, and every internal raise site has been promoted to one of six typed subclasses:

| Subclass | When it's raised | Exit code |
|---|---|---|
| `OptionsError` | Unknown option path, dep cycle, fragment conflict | 2 |
| `FragmentError` | Fragment dir missing, malformed `inject.yaml`, missing `deps.yaml` | 2 |
| `InjectionError` | Missing anchor, ambiguous marker, corrupt sentinel | 3 |
| `MergeError` | Three-way merge conflict (reserved for Epic F/H) | 4 |
| `ProvenanceError` | Missing `forge.toml`, manifest corruption | 5 |
| `PluginError` | Plugin load or registration collision | 6 |

Each error carries `code: str`, `hint: str | None`, and `context: dict[str, Any]`. The CLI's `--json` envelope emits all four fields.

**Impact on your code:**

- `except GeneratorError:` continues to catch every forge failure — the alias makes this safe. You don't need to change anything if you only catch the base class.
- `except ValueError:` around `inject_python` / `inject_ts` **breaks** — those injectors used to raise `ValueError` / `FileNotFoundError` and now raise `InjectionError`. Change your handler to `except forge.errors.InjectionError:` (or `except forge.errors.ForgeError:` to catch all forge failures).
- `type(err).__name__ == "GeneratorError"` **breaks** — use `isinstance(err, forge.errors.ForgeError)` or the specific subclass.
- `pytest.raises(GeneratorError, match="...")` continues to work because the subclass is-a `GeneratorError`, and the matched text is unchanged. Tests that want tighter coverage should migrate to `pytest.raises(forge.errors.OptionsError)` (or whichever fits) and `assert err.value.code == OPTIONS_UNKNOWN_PATH`.

**Machine-readable codes.** If you consume forge's `--json` error envelope, the new `code` field lets you switch on specific failure kinds without string matching:

```python
import json, subprocess
result = subprocess.run(["forge", "--config", "stack.yaml", "--json"], capture_output=True)
if result.returncode != 0:
    envelope = json.loads(result.stdout)
    match envelope.get("code"):
        case "OPTIONS_UNKNOWN_PATH":
            ...   # user typo — suggest forge --list
        case "INJECTION_ANCHOR_NOT_FOUND":
            ...   # base template needs an anchor comment
        case "PROVENANCE_MANIFEST_MISSING":
            ...   # wrong directory; not a forge project
```

No codemod ships for this migration — the changes are too coupled to local test style to mechanise safely. Grep your code for `GeneratorError`, decide whether each site wants the base class or a specific subclass, and update in place.

---

## 0.x → 1.0

forge 1.0 is a clean-break release. The high-level shifts:

1. **Schema-first core** — TypeSpec drives CRUD entities; JSON Schema drives the agentic-UI protocol. Hand-written domain and protocol types are replaced by generated files.
2. **AST-aware injection** — text-marker injection is replaced by LibCST (Python) and ts-morph (TypeScript). Users can now reformat generated code freely.
3. **Three-zone merge** — `forge --update` respects user-owned regions. No more silent overwrites or silent skips.
4. **Ports-and-adapters** — integrations are swappable at runtime. Config change, not regeneration.
5. **Plugin surface** — third parties can ship backends, frontends, fragments, commands, and emitters via `importlib.metadata` entry points.

The overall migration path:

```bash
# 1. Pin your current forge version
forge --version                            # note this

# 2. Re-generate or migrate
forge migrate                              # new 1.0 umbrella command (when available)
#   OR, for a clean break:
forge new --config forge.yaml              # regenerate in a fresh directory
```

## Per-phase breaking changes

This section is populated as each 1.0 alpha ships.

### 1.0.0a1 — Phase 0 foundations (unreleased)

- **CLI entry point** — `forge.cli:main` → `forge.cli.main:main` (same `forge` console script). Code importing `from forge.cli import main` should continue to work via re-export, but code importing private helpers (`_build_parser`, `_Resolver`, etc.) must update to the new paths.
- **`forge.toml`** — new `[forge.provenance]` table. Old projects lacking it will receive a one-time backfill with a warning on the first `forge --update` in 1.0.

### 1.0.0a2 — Phase 1 schema-first (unreleased)

_To be populated when Phase 1 alpha ships._

### 1.0.0a3 — Phase 2 extensibility (unreleased)

_To be populated when Phase 2 alpha ships._

### 1.0.0a4 — Phase 3 agentic-UI upgrade (unreleased)

_To be populated when Phase 3 alpha ships._

### 1.0.0b1 — Phase 4 polish (unreleased)

_To be populated when Phase 4 beta ships._

---

## 1.1 → 1.2 — auth-stack rebuild (unreleased)

The 1.2 release replaces the legacy Keycloak-direct auth stack with
the platform-auth model: Gatekeeper as sole token authority (ES256
internal JWTs), per-language verifier SDKs (Python / Node / Rust),
BFF Redis sessions with a single opaque cookie, inactivity-based
session timeout, and frontend session-timeout composables for Vue /
Svelte / Flutter. See `docs/auth-architecture.md` for the model;
this section is the migration procedure.

This is a **breaking change** for projects generated under 1.1.x —
both the source layout (new `sdks/platform-auth*/` directories,
new gatekeeper modules) and the env-var shape (some `KEYCLOAK_*`
keys move to `GATEKEEPER_*`).

### Migration steps

```bash
# 1. Bump forge.
uv tool upgrade forge

# 2. Plan the migration — dry run, no writes.
cd <generated-project>
forge --migrate --migrate-only auth-keycloak-to-platform-auth --dry-run

# Reports:
#   - Files to add (sdks/platform-auth/, sdks/platform-auth-node/,
#     sdks/platform-auth-rs/, expanded infra/gatekeeper/)
#   - Files to replace (per-backend service/security/auth.* modules,
#     middleware/tenant.{ts,rs} → middleware/auth.{ts,rs})
#   - infra/keycloak-realm.json additive changes (tenant-id mapper,
#     serviceAccountsEnabled on gatekeeper client, dev user attribute)
#   - docker-compose.yml service changes (gatekeeper-keygen init,
#     extended gatekeeper env block)
#   - Env var renames (KEYCLOAK_* → GATEKEEPER_* per the table below)

# 3. Apply the codemod. Three-way merge against your edits;
#    .forge-merge sidecars on conflict (resolve by hand).
forge --migrate auth-keycloak-to-platform-auth

# 4. Inspect any sidecars produced.
git status | grep .forge-merge

# 5. Rebuild + boot.
docker compose up --build
#   - gatekeeper-keygen runs first, generates ECDSA P-256 keys
#   - gatekeeper boots, /auth/jwks serves the public key
#   - backends pick up the new SDK from sdks/platform-auth*/
#   - browser flow: login at Keycloak, callback issues session_id cookie
```

### Env var renames

| Before (1.1.x) | After (1.2.x) | Notes |
| --- | --- | --- |
| `KEYCLOAK_BASE_URL` | `KEYCLOAK_BASE_URL` | Still consumed by gatekeeper for the OIDC bridge. |
| `KEYCLOAK_REALM` | (gone) | Encoded in `KEYCLOAK_BASE_URL` path now. |
| `KEYCLOAK_CLIENT_ID` | `GATEKEEPER_CLIENT_ID` | Was the per-service client; now the gatekeeper's confidential client. |
| `KEYCLOAK_CLIENT_SECRET` | `GATEKEEPER_CLIENT_SECRET` | Same scope shift. |
| `APP__SECURITY__AUTH__SERVER_URL` | `GATEKEEPER_ISSUER` | Backends verify against gatekeeper's JWKS, not Keycloak's. |
| `APP__SECURITY__AUTH__REALM` | (gone) | Subsumed by `GATEKEEPER_ISSUER` (single trusted issuer). |
| (new) | `INTERNAL_TOKEN_AUDIENCE` | aud claim on minted JWTs. Defaults to `forge-services`. |
| (new) | `SESSION_FERNET_KEY` | Required. Generate via `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`. Rotation invalidates all live sessions. |
| (new) | `SESSION_TIMEOUT_ENABLED` | Defaults to `true`. Set `false` to skip idle/absolute checks (still issues internal JWTs). |
| (new) | `DEFAULT_IDLE_TIMEOUT_SECONDS` | Default `1800` (30 min). Per-tenant overridable via `TenantConfig`. |
| (new) | `DEFAULT_ABSOLUTE_TIMEOUT_SECONDS` | Default `43200` (12 h). |
| (new) | `SESSION_WARN_AT_SECONDS` | Default `60`. SPA modal threshold. |
| (new) | `SERVICE_REGISTRY_PATH` | argon2id-hashed S2S client secrets. |
| (new) | `KEY_BACKEND` | `file` (default). `aws_kms` / `vault` are follow-ups. |
| (new) | `SIGNING_KEY_DIR` | Where `gatekeeper-keygen` writes keys. Default `/run/secrets/gatekeeper-signing`. |

### Cookie changes

The browser cookie surface contracts from two cookies to one:

| Before | After | Notes |
| --- | --- | --- |
| `tenant_session=<jwt>` | (gone) | Access tokens no longer leave the server. |
| `tenant_refresh=<jwt>` | (gone) | Refresh tokens stay in Redis. |
| (new) | `tenant_session_id=<24-byte-random>` | `HttpOnly`, `Secure`, `SameSite=Lax`, `Max-Age=absolute_timeout_seconds`. |

`SameSite=Lax` (not `Strict`) — preserves deep-linking from external
tools. CSRF is mitigated at the API layer via
`Content-Type: application/json` enforcement on mutating endpoints.
If your project added a non-JSON mutating endpoint, audit it before
upgrading or you'll lose the CSRF guard silently.

### Hard cutover, NOT dual-mode

The codemod replaces the auth stack atomically — no flag toggles
the old vs new behaviour. Existing sessions are invalidated at
deploy. Schedule a maintenance window and announce it; the new
stack boots in under a minute on a warm machine.

### Rollback

If something breaks post-migration, the cleanest rollback is:

```bash
git checkout <pre-migration-commit>
forge --update                # re-applies the 1.1.x baseline
docker compose up --build
```

`forge --update` is idempotent and respects user edits via
`.forge-merge` sidecars, so this is safe even with concurrent
in-progress work.

### Behavioural changes engineers should know

1. **`/auth` does NOT extend the session.** Every authenticated route
   passes through `/auth` (Traefik's ForwardAuth), but the call is
   read-only — no idle-TTL touch. Sessions extend exclusively when
   the SPA POSTs `/auth/session` on real user activity (mouse,
   keyboard, scroll, visibility). New code that polls a backend in
   the background has zero session impact, by design. Document this
   in your `AGENTS.md` / `CLAUDE.md` so a future contributor "fixing"
   an unexpected logout by adding a heartbeat poll doesn't defeat
   the compliance posture.
2. **Internal JWT TTL is 5 minutes.** A token revoked upstream stays
   verifiable for up to 5 minutes after `/logout` —
   `internal_token_cache.evict_for_sub` is best-effort. Engineers
   building features with hard revocation requirements (e.g.,
   financial-impact actions) need to gate on the session itself,
   not the bearer.
3. **Scope-based authz is now first-class.** Endpoints can declare
   `requireScope("things:read")` (or the language-equivalent) and
   AuthGuard rejects with a typed `ScopeRequired` error carrying
   the missing scopes. Wildcards (`things:*`, `*`) are honored.
4. **S2S calls go through `S2SClient`**, not raw `httpx`/`fetch`/`reqwest`.
   The client handles client_credentials and RFC 8693 token-exchange
   automatically and caches the resulting tokens.

---

## Codemods and tooling

When a mechanical migration is possible, forge ships a `forge migrate-<x>` codemod:

| Codemod | Availability | What it does |
|---|---|---|
| `forge migrate` | Post-1.0.0a1 | Umbrella — runs all applicable migrations for a project |
| `forge migrate-entities` | Post-1.0.0a2 | Translate hand-written domain/*.py, prisma/schema.prisma, models.rs to a generated `domain/*.tsp` |
| `forge migrate-ui-protocol` | Post-1.0.0a2 | Delete hand-written `types.ts` / `chat.types.ts` / `agent_state.dart`; re-run generator |
| `forge migrate-adapters` | Post-1.0.0a3 | Restructure `src/app/rag/` into `src/app/ports/` + `src/app/adapters/vector_store/` |

Each codemod is idempotent and safe to re-run.

## Rollback

If an upgrade fails, every alpha/beta retains an installable identity on PyPI. Rollback is:

```bash
uv pip install "forge==0.X.Y"    # your last working version
```

and discard the `1.0-dev` workspace. The `0.x-final` tag is the stable reference.
