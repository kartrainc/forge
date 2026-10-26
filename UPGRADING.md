# Upgrading forge

This document lists breaking changes per version and the migration steps for each.

## 1.1 → 1.2

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
- **Web frontends:** use Node 22.18 or newer. Vite 8 uses Rolldown build options.
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
untouched screens are rewritten. The scaffold only Nuxt UI emits stays on disk
without a forge record:

- `src/app/providers/head.ts` and `head.test.ts`, and `src/custom/theme/`.
  They import `@unhead/vue` and `@nuxt/ui`, which shadcn projects do not
  depend on, so the type-check and the unit suite fail until you delete them.
- The agent tooling: `.agents/skills/nuxt-ui/`, `.claude/skills/nuxt-ui/`,
  `.mcp.json` and `.codex/config.toml`. Delete it, or keep what you added to
  the MCP files elsewhere: while any of them remains unrecorded, a later
  switch to Nuxt UI stops on the collision.

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
  an undeclared variable, a missing source or two different policies stop the
  check. Until the first report arrives, the script serves each page with a
  canary script the policy blocks, and the check fails without its report, so
  a policy that reports nowhere cannot pass. Both scripts are written only
  when absent and are the project's afterwards: give the config the specs,
  projects and builds a production run can serve (a development-only route or
  a second build is the project's to arrange); to take a later version,
  delete yours before updating.
- A project that keeps its own policy and adopts the nonce rewrites the
  placeholder in `index.html` wherever the page is served: in `nginx.conf` and
  in the Helm chart's server block, or every script is blocked.
- Svelte, Flutter and plugin frontends, and Vue projects with the AI chat, are
  served as before, without a policy. The chat's MCP panels load remote
  origins in frames, which the policy would block.

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
