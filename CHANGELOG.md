# Changelog

All notable changes to forge are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] — targeting 1.2.0

### Dependency compatibility

- Coordinate weekly dependency updates in one cross-ecosystem PR, including
  standalone SDK manifests; refresh the generator and three emitted Python locks.
- Exercise all authentication parity runners with installed runtime dependencies,
  repair incomplete negative-token fixtures, and upgrade Rust JWT verification to
  jsonwebtoken 11.1 with AWS-LC. Malformed optional `nbf` claims are rejected.
- Migrate generated Node services to Zod 4, Vitest 5 and Prisma 7's PostgreSQL
  adapter, preserving pool and schema settings. Patch its transitive MySQL tooling.
  Upgrade Node authentication to JOSE 6 and test the complete SDK in CI.
- Upgrade generated Rust services to SQLx 0.9, reqwest 0.13, tower-http 0.7 and
  config 0.15; use parameterized query builders and declare Rust 1.94 minimum.
- Refresh optional queue, cache, telemetry and AI adapters, including dependency
  pins in feature registrations. Adapt AI streaming events without duplicating
  tool-call arguments, and exercise the generated Node/Rust port configurations.
- Migrate Flutter widgets to Material UI, preserve Markdown theme/localization
  compatibility, and require actual generated widget tests and analyzer success.
  Repair missing mock fallbacks and keep environment tests runnable without auth.
- Add Svelte canvas type checks and checkbox submission tests. Use recorded
  pre-format snippet fingerprints to avoid spurious harvest conflicts while
  preserving conflicts for genuine upstream changes. Exclude local SDK coverage
  and compilation artifacts from generation and distribution.
- Upgrade web frontends to Vite 8, SvelteKit 3, Vue Router 5 and TanStack Table 9.
  Validate real TypeScript projects, generated OpenAPI clients and table behavior.
  Instrument browser coverage using the compiler's source maps while retaining
  strict coverage checks. Preserve column preferences and streamed tool arguments.
  Run frontend checks after composition supplies generated clients and protocols;
  propagate failures with both stdout and stderr diagnostics.
  Keep explicit OpenAPI generation in `src/custom/api/`; starting the dev
  server preserves generic runtime and configured feature types.
  Protect Svelte's `src/lib/shared/` runtime with the same regeneration checks
  as Vue's `src/shared/`, including rejection of locally restamped overrides.

- Align AG-UI client and core at 1.0.2 across the generator, canvas packages,
  and npm lockfile; group their future Dependabot updates together.
- Include the Svelte canvas build and runtime tests in workspace commands;
  run all canvas tests, including a real AG-UI streaming fixture, in CI
  and dependency upgrade probes.
- Upgrade the development type checker to ty 0.0.85 after its canary passes;
  model renderer metadata as read-only and narrow optional fragment names.
- Use the supported `shutil.rmtree(onexc=...)` callback for read-only cleanup.
- Raise the Copier and Hypothesis minimum versions to 9.18.2 and 6.168.4,
  respectively, and keep their lockfile requirements synchronized.
- Use Dependabot's uv ecosystem so Python updates include `uv.lock`.
- Refresh the pinned setup-uv, setup-go, Node, artifact and release actions;
  preserve CI cache pruning and build releases with a fresh dependency cache.
- Replace the inactive Codecov integration with retained GitHub HTML/XML/JSON
  coverage reports and revision-linked run summaries. Upload failures are fatal;
  existing generator and generated-application coverage gates remain enforced.
- Record nightly status from the gated execution rather than rerunning scenarios
  for reporting, and tear down partial Compose stacks on failure. Match Rust
  builder/runtime libc versions and validate shipped frontend lint commands.
- Smoke-test release preparation in isolated temporary directories and build
  the SBOM from Forge's locked runtime environment without development tools.
- Compare nightly round-trip recipes independently of the sandbox generator
  identity while retaining checks for configuration, version, and plugin drift.
- Make nightly update checks enforce the rejection of partial/overwrite modes
  for ownership-managed projects and verify that rejected updates change no files.
  Finish Git's automatic maintenance before generation returns, so background
  object repacking cannot race the unchanged-file check.

### Vue UI kits

- Add `frontend.ui_kit` (`--ui-kit`) to choose the Vue template's component
  library. `shadcn` stays the default ([ADR-011](docs/architecture-decisions/ADR-011-ui-kit-option-for-the-vue-template.md)).
  Layouts, component features and fragments declare their kits; forge refuses
  an incompatible combination before writing, naming the replacement component.
- Render the Vue template's no-chat `App.vue` and `MainLayout.vue` and its
  no-Keycloak `useAuth.ts` from Copier conditional file names instead of
  post-generate strings; generated output is unchanged.
- Make `nuxt-ui` selectable: the template renders its shell, screens and CRUD
  pages on Nuxt UI 4 with its colour mode, bundled lucide icons and a brand
  module in `src/custom/theme/` that the project owns. It takes the sidebar
  layout without the AI chat; `--color-scheme` also takes Tailwind palette
  names for it. A `vue-nuxt-ui` lane of the generated-quality check renders it
  with authentication and two CRUD features.
- Let a fragment ship a `files-<kit>/` tree whose files replace the same paths
  of `files/` for that kit; the session-timeout dialog uses it for Nuxt UI.

### Fixed

- `security.csp` now protects the Vue frontend. The frontend's `nginx.conf` and
  the Helm chart's default server block send a nonce-based
  Content-Security-Policy with `index.html` and defence-in-depth headers with
  every response; the build stamps the nonce placeholder nginx replaces per
  response. The standalone `deploy/infra/nginx-csp.conf`, which nothing
  included and which named an undefined nginx variable, is no longer emitted.
  Both Vue lanes run the browser suites against the production build under the
  policy and fail on any violation.

### Platform routing

- Route new `microservices` and `headless-api` projects directly from the edge
  to domain services; remove the intermediary gateway from their defaults.
  Microservices retains `orders → inventory` S2S grants; headless API retains
  edge authentication with one orders backend and no S2S discovery.
- Add the opt-in Python `service-proxy` template name, keeping `api-gateway`
  compatible. Distinguish URL routing, service-token forwarding, and custom
  backends for frontends in the architecture diagrams and platform guide.
- Preserve service lists recorded in quality recipes during `forge --update`;
  explicit backend lists also override preset defaults during generation.
  Preset-only configurations use the installed defaults when regenerated; see
  [migration guidance](UPGRADING.md#direct-routing-defaults) for legacy projects.

### Generated code quality

- Record reproducible generation recipes and generated/scaffold/user ownership.
  Regeneration checks protect generic runtime and schema output independently
  of editable local hashes; import and mutation checks enforce extension boundaries.
- Add transactional ownership-aware updates, conflict proposals and explicit
  headless conflict resolution. Legacy projects receive a migration proposal.
- Add native Python, Node, Rust, Vue, Svelte and Flutter coverage adapters.
  Require passing unit, integration and E2E evidence and strictly greater than
  80% changed-line coverage independently for services, frontends and shared packages.
- Repair generated service CI paths, frozen installs, Node test discovery and
  required codegen failures; canonicalize Python before recording provenance.
  Add real backend lifecycle/database tests, browser preference journeys and
  generated wire-contract tests. Redact Rust internal errors in public responses.
  Wire Node pool settings into Prisma; use Vitest 5 and a patched Prisma
  configuration dependency (GHSA-ggr8-5vv4-36mx).
- Exercise generated Vue/Svelte CRUD, shared UI and responsive layouts in native
  suites. Preserve Svelte backend routing and navigation without authentication;
  preserve Vue request bodies during session refresh and confirmation results.
  Keep generation-time checks independent of integration database fixtures.
  Resolve npm 10's Vitest optional-peer conflict and update vulnerable Python locks.
- Ship one shared `forge-platform` skill for Claude and Codex, and a deterministic
  workload recommender constrained by the live capability registry.
- Disable Claude co-author trailers, reject new trailers in CI, and provide a
  backup-first, tree-verified history cleanup command. Historical publication
  remains a separate maintenance operation.

### Deployment

- **Topology-aware Helm chart that stays current on `forge --update`.**
  `deploy.target=kubernetes` now emits a single Helm umbrella chart under
  `deploy/helm/` (replacing the old static single-service chart + the
  `deploy_kubernetes`/`deploy_k8s_hpa` raw-manifest fragments). The chart's
  `values.yaml` is rendered from the project's deployment topology — one
  `workloads` entry per backend (correct language/port/migrations) plus the
  frontend and platform-service toggles — so it produces one Deployment /
  Service / HPA per backend, a frontend Deployment, an Ingress (replacing the
  dev-only Traefik), and per-backend ConfigMap + Secret. The chart bodies are
  pure Go-templates that `range` over `.Values.workloads`, so multi-backend
  works without per-service template duplication. Datastores are external by
  default (managed Postgres/Redis/Keycloak via `externalServices`); set
  `infra.inCluster=true` for throwaway in-cluster stand-ins. DB migrations run
  as `pre-install`/`pre-upgrade` Helm hook Jobs; S2S/DB secrets are `CHANGEME`
  placeholders (never baked). Validated by `helm lint` + `helm template` +
  `kubeconform -strict` in a new `deploy-helm` CI job.
- **Keep-current plumbing.** The chart is a project-scope fragment, so it rides
  the existing `apply_project_features` → three-way-merge → provenance rail: it
  is re-rendered from the live topology on `forge --update` and user edits to
  the forge-owned `values.yaml` are preserved (or surfaced as a `.forge-merge`
  sidecar). `forge.config._topology.compute_topology` is the single source of
  truth shared with `docker-compose.yml` (so the two can't drift); the updater
  now recovers each backend's `server_port` from `.copier-answers.yml` so the
  re-rendered chart keeps the right container ports.
- **`deploy/` boundary.** Generated projects now separate application code
  (`services/`, `apps/`, `packages/`) from deployment artifacts under `deploy/`
  (`helm/`, `k8s/`, `compose/`, `infra/`):
  - `init-db.sh` moved to `deploy/compose/`; a root `Makefile` wraps the deploy
    commands and derives `deploy/k8s/` raw manifests from the chart via
    `helm template` (so they can't drift).
  - The project-root SDK tree (`platform-auth`, etc.) moved from `sdks/` to
    `packages/`, matching the `apps/`/`services/`/`packages/` monorepo
    convention (the backend-local `services/<svc>/sdks/forge-core` is unchanged).
  - The auth-owned Keycloak + Gatekeeper sources and the realm JSON moved from
    `infra/` to `deploy/infra/`.
  - The frontend `nginx.conf` is no longer baked into the image — it is mounted
    (a compose bind-mount; a ConfigMap in the chart), so routing config is a
    deployment concern.
- **In-cluster Gatekeeper (dev/demo).** With `infra.inCluster=true` +
  `infra.gatekeeper.enabled=true` the chart emits an in-cluster Gatekeeper
  (keygen initContainer for signing keys + a post-install realm-sync Job).
  Off by default — production uses an external/managed Gatekeeper.

### Distribution

- **GitHub-only distribution — no package registries.** forge is no longer
  published to PyPI/npm/pub.dev; it installs from source via `./install`
  (`uv tool install git+<repo>`). The registry publish jobs were removed from
  `release.yml` and `release-dryrun.yml` was deleted — a tag now cuts only a
  GitHub Release (built artifacts + CycloneDX SBOM + changelog notes), still
  gated by the tag↔version check. Fixed the macOS install-script abort (the uv
  installer's non-zero exit under `set -o pipefail`). `RELEASING.md` rewritten
  and RFC-003 marked superseded.

### Security & regression fixes (wave 3)
- **`forge --update` works on stateless/headless projects** (#274, fixes #260). Forge crashed when updating projects with `database.mode=none` or `frontend.mode=none` because `from_legacy_options` forwarded mode-inapplicable layer sub-fields (e.g. `database.engine` when `database.mode=none`, or `frontend.api_target.*` when `frontend.mode=none`) into the typed config, where `DatabaseNone`/`FrontendNone` declare `extra="forbid"`. The converter (`forge/config/typed_config.py`) now gates those fields on the effective mode, and the generator (`forge/generator.py`) drops the dead defaults before persisting, so newly generated `forge.toml` files stay honest and update-safe.


- **Two-stage application-template variants render a non-empty `app.title`.**
  A variant overlay (e.g. `tenant-management-service`) ships its own
  `copier.yml` that declares no questions, so it never computed copier's
  `project_title` `when:false` default — its `config/default.yaml` rendered
  `title: ""`, and the generated FastAPI app aborted at boot ("A title must be
  provided for OpenAPI"). This was the chronic `multitenant-saas` nightly
  compose-boot failure (the `tms` control-plane service crash-looped).
  `variable_mapper.backend_context` now supplies `project_title` directly, so
  every render stage (base + overlay) gets it. Built-in single-stage renders
  are byte-identical (the value matches what copier computed).
- **Every Rust capability port now coexists on one backend** (#235, #236). The
  `llm_port`/`llm_openai` Rust fragments shipped a full `src/ports/mod.rs` and
  `src/adapters/mod.rs` that overwrote the base template's shared files — so
  even an llm-only Rust project failed to generate, and a `conflicts_with`
  mutex blocked llm + queue/cache combos. They now INJECT `pub mod llm;` /
  `pub mod llm_openai;` into the shared mod.rs (like queue/cache), retiring the
  mutex; llm + queue(apalis) + cache + webhooks all generate and compile on a
  single Rust backend. The apalis consumer was rewritten off the private
  `RedisStorage::fetch_next` onto apalis 0.6's public `WorkerBuilder` API
  (a background worker feeds the `QueuePort::consume` stream via a channel).
  The `rust_ports` matrix scenario now exercises all four together
  (build + clippy + fmt) instead of webhooks-only.
- **Generated Keycloak projects boot again.** The realm-sync sidecar pinned
  `ENV=development` so its fail-closed guard no longer rejects the shipped dev
  `KC_ADMIN_PASSWORD` under the image's baked `ENV=production`, restoring
  `docker compose up` for every `include_keycloak` project.
- **Node/Rust full-stack images build again.** The per-backend Dockerfile
  `COPY --from=sdks` is gated on the backend's OWN-language platform-auth
  middleware, not Python's — a Node/Rust-only auth project no longer ships
  `sdks/` without the COPY.
- **`worker` app_template generates with default options** via
  `excluded_app_templates=("worker",)` on the HTTP-shaped default fragments
  plus a resolve-time guard for explicit selections.
- **`forge --platform <preset>` runs headless** — `_is_headless` now compares
  the parsed namespace against the no-argv baseline, so every generation flag
  is detected instead of a hand-maintained list.
- **Generated-service security:** chat-files absolute-path LFI + tenant
  scoping closed; webhooks cross-tenant IDOR + test-fire SSRF closed; the
  `/ws/agent` WebSocket is authenticated; the `in_memory` dev issuer refuses
  to boot in production; the CloudEvent stream is auth-gated; CORS
  wildcard-with-credentials and the gatekeeper test-bypass fail closed in
  production; dev-compose infra ports bind to loopback. A discovery-based
  route-auth lint now requires every generated router to be gated or
  explicitly public.
- **Chat authenticates under the BFF.** The AG-UI client sends
  `credentials: 'include'`, so a chat turn no longer 401s when auth is on.

### Packaging

- **Repository is now MIT-licensed** (root `LICENSE`, `pyproject` metadata).
- **PyPI distribution name is `forge-cli`** (the `forge` name is owned by an
  unrelated package); the installed console command stays `forge`. RFC-003.
- **`forge --version`** prints the version and exits 0.
- **Pinned floating base images in generated projects** (#223): the dev
  `pgadmin` service is `dpage/pgadmin4:9` (was `:latest`) across all compose
  templates, and the worker Dockerfile's uv stage is `ghcr.io/astral-sh/uv:0.11.16`
  (matching the main service Dockerfile) — no more silent `:latest` drift.
- **Generated k8s probes now hit the right port.** The raw Kubernetes
  `deployment.yaml` hardcoded `containerPort: 8000` while the backend listens on
  the configured `server_port` (default 5000) — so the liveness/readiness
  probes (which target the named `http` port) failed for any non-8000 project.
  `containerPort` now renders from `server_port`, and the Helm chart's
  `values.yaml` `targetPort` default follows the primary backend's port too
  (threaded into the project-scope render context). Health paths
  (`/api/v1/health/{live,ready}`) already matched the Dockerfile HEALTHCHECK.
- **Reproducible dependency installs via committed lockfiles** (#223): the
  generator now resolves a workspace-root `package-lock.json` (Node) /
  `Cargo.lock` (Rust) at creation time (`_generate_lockfiles`, toolchain-gated
  and best-effort), and the service Dockerfiles install frozen — `npm ci` and
  `cargo build --locked` when the lockfile is present, falling back to
  `npm install` / unlocked `cargo build` when it isn't. Builds now pin the
  exact transitive versions forge resolved instead of drifting with registry
  HEAD. Lockfiles are committed but stay snapshot-excluded (host-asymmetric)
  while remaining visible to the round-trip diff. The frontend image install
  is frozen too (`npm ci` / `<pm> install --frozen-lockfile` per package
  manager), and the generated CI workflows now install frozen (`npm ci`,
  `cargo … --locked`) so a stale lockfile fails in CI rather than only in the
  prod Docker build.

### Internal

- **Removed the never-adopted `PortSpec` / `service_registration` seam.** The
  Pillar A.4 / RFC-009 renderer scaffolding (`forge/service_registration.py`,
  `forge/specs/port.py`, the `_shared/service_registration` + `_shared/port_spec`
  templates, and their tests) had zero consumers — no `Fragment` ever populated
  a `PortSpec`, and the shared-`ports/mod.rs` collision it was meant to solve
  was instead handled by `inject.yaml` markers (#236). RFC-009 is marked
  superseded. The generic `FragmentRenderer` protocol and `MiddlewareSpec` (its
  one live implementer, via `Fragment.middlewares`) are unchanged.

### Plugin SDK

- **SDK 1.6:** `ForgeAPI.add_frontend_layout` takes `ui_kits`, the Vue UI kits
  a plugin layout renders with, as a `layout.toml` declares them.
- **`add_backend` now genuinely generates** (#224): registering a backend
  language through `ForgeAPI.add_backend` produces a working project end to
  end, not just a registry entry. The paths that coerced the language string
  with `BackendLanguage(...)` — crashing on anything but python/node/rust —
  now resolve through `resolve_backend_language`: the forge.toml writer, the
  config-file `backends:` parser, the `add-backend` command, the `--update`
  template-version walk, the variable-mapper context builder, and the
  interactive backend prompt. `add_backend` also seeds the language's default
  `crud-service` application-template variant so a `BackendConfig` validates
  out of the box; the CLI's `--backend-language` / `--add-backend-language`
  choices are built after plugin load (selectable + shown in `--help`); the
  `--update` backend scan infers plugin backends from their `.copier-answers`
  `_src_path`; and the root compose no longer emits a migrate sidecar for a
  stateless plugin backend (which would have deadlocked `docker compose up`).
- **Reference Go backend plugin** (`examples/forge-go-backend`): a worked
  example that registers a Go (net/http) backend language — template shipped
  as package data, an absolute `template_dir`, and a `BackendToolchain`
  wiring `go build`/`go vet`/`gofmt`. Exercised by `tests/test_plugin_backend_go.py`,
  which generates a Go project and compiles it.
- **`add_frontend` now genuinely generates** — the frontend twin of the
  `add_backend` fix. A plugin-registered framework used to crash (`No mapper`
  in the variable-mapper) or render nothing (`_generate_frontend` only knew
  the built-in `TEMPLATE_DIRS`). The frontend dispatch now consults
  `FRONTEND_SPECS` for plugin frameworks: a generic `plugin_frontend_context`
  mapper, template-dir lookup in `_generate_frontend` + the forge.toml writer,
  the `--frontend` CLI choices + interactive prompt built after plugin load,
  the `--update` manifest resolution, and the npm-workspace / frontend
  Dockerfile wiring (driven by new `FrontendSpec.node_based` / `build_dir` /
  `package_manager` fields). A config-file `frontend.framework` now resolves
  through `resolve_frontend_framework` instead of silently coercing to `none`.
- **Reference Vite frontend plugin** (`examples/forge-vite-frontend`):
  registers a Vite (vanilla-TS) frontend framework. Exercised by
  `tests/test_plugin_frontend_vite.py`, which generates the SPA and builds it
  (`npm install` + `npm run build` → `dist/`).

### Recent merges (post-2026-05-23, previously unlogged)

- **Platform generator (#170):** pluggable auth providers
  (`gatekeeper`/`in_memory`/`oidc_generic`), a backend-template registry, and
  multitenancy, with weld-free standalone builds.
- **Selectable frontend layouts (#169):** six app-shell layouts across
  Vue/Svelte/Flutter via template variants + `layout.toml` discovery.
- **AG-UI SSE transport:** the `agent_agui` fragment serves the canonical
  `POST /api/v1/agent` AG-UI SSE run protocol the generated frontend consumes.
- **Token-claim multitenancy (#192–#194):** `schema_per_tenant` binds
  `search_path` post-auth via the UoW; `shared_rls` binds the account GUC;
  RLS fails closed on an empty GUC. Real-Postgres isolation tests added.
- **Platform back-port tranche (#171–#182, #195):** primitives back-ported
  from the platform line, including `Checkbox`/`Popover` UI components.

### Added

- **Read-only verb manifest cache-scope (#275).** Manifest reads on `--verify`, `--resolve`, `--accept-harvested`, and `--reapply-baseline` now install the same `manifest_cache_scope` (from `forge.sync._manifest_cache`) already used by `--update` and `--harvest`, so repeated `forge.toml` parses within a single invocation (especially in `--accept-harvested`'s per-block-candidate baseline lookups) pay once. Performance win on large, multi-backend projects. (`forge/cli/commands/{verify,resolve,accept_harvested,reapply_baseline}.py`.)

- **Platform preset fuzzer (#275).** New `tests/fuzz/test_preset_combinations.py` deterministically resolves every shipped `--platform` preset (a fast smoke that fails the moment a preset stops building a consistent plan) and fuzzes each one under hypothesis-perturbed bool/enum options, asserting the resolver always reaches a consistent plan or fails validation cleanly — so a broken preset can't ship unnoticed. Runs in the `fuzz` quality-gate lane (`pytest -m fuzz`).

- **Nightly-failure scenario naming in the GitHub issue (#275).** The auto-filed `nightly-failure` issue now splices a `Failing scenarios` table (scenario / lane / detail) into the body, naming each failing scenario instead of leaving only the per-lane pass/fail grid — unblocking root-cause investigation. Powered by a new stdlib-only script at `scripts/ci/nightly_failure_summary.py` (wired into `matrix-nightly.yml`).

- **Doc-truth CI gate (#275).** New `tests/test_doc_truth.py` validates mechanical doc claims — README version/Python/registry (backends/frontends/options) badges, relative-link resolution, and that documented `forge` CLI flags actually exist in the parser. Wired into the `lint` job in `ci.yml`. It caught and fixed real drift: `forge --plan-migrate`, `forge migrate --only`, and `forge migrate --skip` referenced in `UPGRADING.md` and `docs/operations/maintainer-runbook.md` were never valid flags (canonical is `forge --migrate [--migrate-only NAMES] [--migrate-skip NAMES]`). Also adds `tests/mutmut_known_survivors.md`, the survivor log referenced by `docs/operations/mutation-testing.md`.

- **Generated service runbooks (OPERATIONS.md + OBSERVABILITY.md) (#276).** Every generated Python/Node/Rust backend now ships two template files under `forge/templates/services/*-service-template/template/`: `OPERATIONS.md` (config precedence/load order, database migrations — with the Postgres advisory-lock boot pattern on Python/Node and the explicit-`sqlx::migrate!` note for Rust — graceful shutdown choreography, and the `/api/v1/health/{live,ready}` endpoint contract) and `OBSERVABILITY.md` (OTLP env vars such as `OTEL_EXPORTER_OTLP_ENDPOINT`, RED metric definitions, Prometheus/Grafana dashboard setup, and structured-logging conventions). Operators now have canonical per-backend reference material instead of ad-hoc docs (previously only `tenant-management-service` shipped a `HARDENING.md`). A regression-guard test (`tests/test_generated_ops_docs.py`) asserts each base template emits both files with non-stub content and no unrendered Jinja.

- **Rust observability.otel real OTLP implementation (#279).** The `observability_otel` fragment on Rust backends now ships a genuine OTLP/HTTP tracing layer (opentelemetry 0.27 + tracing-opentelemetry 0.28) instead of the legacy no-op stub. The base `main.rs` builds an open, boxed tracing-layer set (`Vec<Box<dyn Layer<Registry> + Send + Sync>>`) so the otel fragment can push its layer at the `FORGE:TRACING_LAYERS` anchor without the base template depending on otel crates; the fragment adds the opentelemetry deps at the existing `FORGE:CARGO_DEPENDENCIES` marker and exports spans over OTLP/HTTP when `OTEL_EXPORTER_OTLP_ENDPOINT` is set (no-op otherwise — the layer builder returns `None` and the service runs without a collector). `OTEL_SERVICE_NAME` sets the resource service name.

- **Flutter native chat authenticates with bearer token (#278).** The `chatAuthTokenProvider` in the Flutter frontend template did not surface an access token, so native (mobile/desktop) Flutter chat never attached an `Authorization: Bearer` header even when `include_auth=true`. The provider now returns the access token from `authRepositoryProvider` (when auth is enabled), and `chat_input_bar` threads it into `sendMessage` via `bearerToken`. Web Flutter continues to receive `null` (Gatekeeper HttpOnly cookies handle auth there, so the bearer is omitted). No-auth projects (`include_auth=false`) keep the `null` no-op path with no auth import. Complementary fix to the existing cookie-based BFF auth for web clients.

- **`GET /mcp/audit?limit=N` — read-side audit endpoint (Pillar F.5).**
  Adds a read endpoint to the MCP server router in generated Python
  projects (shipped via the `mcp_server` fragment, pulled in by
  `agent.mode=tool_calling`). Returns the last `N` (1–1000, default
  50) entries from the JSONL audit log written by
  `record_invocation`, most-recent-first, as
  `{"entries": [{ts, user_id, server, tool, input_hash, decision,
  error}, ...]}`. A new `read_last_n(limit)` helper on
  `app.mcp.audit` backs the endpoint and is independently importable
  for operators who want to consume the audit stream from a sidecar.
  Missing log file (no calls yet) returns an empty list; storage IO
  failures surface as `500`; invalid `limit` surfaces as `422` via
  FastAPI's `Query` validator. **Backwards-compatible** — the
  existing write path (`record_invocation` + on-disk JSONL format) is
  unchanged; the endpoint is purely additive. Closes the long-
  standing gap where `audit.py` was write-only and operators had to
  `tail`/`grep` the on-disk file by hand.

- **Fragment-DX cheap wins (Horizon 1).** Three small additions to the
  fragment authoring surface that together cut ~20% off the multi-
  language fragment files (`object_store`, `llm_*`, `vector_store_*`).
  `Fragment.shared_env_vars: tuple[tuple[str, str], ...] = ()` declares
  backend-agnostic env vars (`AWS_REGION`, `S3_ENDPOINT_URL`, …) once
  instead of repeating them per `FragmentImplSpec.env_vars`; the env
  applier merges shared first, per-impl second, with per-impl winning on
  key collision so a single language can still override the shared
  default. `Fragment.before: tuple[str, ...] = ()` and `Fragment.after:
  tuple[str, ...] = ()` declare soft ordering constraints — unlike
  `depends_on` (which is a HARD pull), `before` / `after` only activate
  when both endpoints are already in the plan, so a fragment can say
  "if X happens to coexist, sit before/after it" without forcing X into
  every plan. Numeric `order` remains the tiebreak inside a topological
  layer. Cycles in the combined `depends_on` ∪ `before` ∪ `after` graph
  are caught at `FRAGMENT_REGISTRY.freeze()` with the cycle path
  surfaced in the `FragmentError`. The accompanying docstring sweep
  retargets `forge.feature_injector`-shim references (deleted in
  1.2.0-alpha.1) at the post-shim home: `forge.sync.forge_to_project.
  updater` for the orchestrator, `forge.appliers.*` for the body
  helpers. All three new fields default to empty tuples so every
  existing fragment registration is byte-identical.

- **Fragment-DX cheap wins follow-through (Horizon 1).** Contract-test
  surface at `tests/test_fragment_cheap_wins.py` (18 tests in 3
  classes — `TestSharedEnvVars`, `TestBeforeAfterOrdering`,
  `TestNoFeatureInjectorReferences`) acts as the public-facing
  release gate for the cheap-wins ship. Docstring sweep across 9
  in-tree modules retargets stale `forge.feature_injector`-shim
  references (deleted in 1.2.0-alpha.1) at the post-shim landings:
  `forge.sync.forge_to_project.updater` for the orchestrator,
  `forge.appliers.*` for the body helpers, and
  `forge.injectors._registry.ApplierRegistry` (Pillar A.1, SDK 1.2)
  for the suffix-dispatch entry point. The deeper end-to-end
  coverage of `shared_env_vars` + `before`/`after` lives in the
  pre-existing `tests/fragments/test_fragment_dx_fields.py`; the new
  file's Shared/BeforeAfter classes are an intentional transitional
  duplication that consolidates back to the dx_fields file in 1.2.1.

- **`llm_port` + `llm_openai` are now tier-1 on Node + Rust (Pillar D.2).**
  Promotes the LLM provider port from Python-only / tier-3 to
  tier-1, with the first cross-language adapter (`llm_openai`)
  shipping alongside. Node uses the Vercel AI SDK (`ai` core +
  `@ai-sdk/openai` provider); Rust uses the `async-openai` crate.
  Both stream `LlmChunk` events behind the same port contract as
  the Python adapter — the TypeSpec spec at
  `forge/templates/_shared/ports/llm/contract.tsp` is the
  cross-language source of truth. Selecting `llm.provider=openai`
  on a Node or Rust backend now resolves both `llm_port` and
  `llm_openai`; mixed-language projects get one port + one adapter
  per supporting backend. **Honest scope** — `llm_anthropic`,
  `llm_ollama`, and `llm_bedrock` stay Python-only / tier-3 in 1.x;
  their SDK ecosystems aren't mature enough on Node/Rust to justify
  in-tree adapters, and plugin authors carry that gap (Featured
  Plugin tier — see `docs/reference/limitations.md`). The Pillar A.4
  `PortSpec` infra is intentionally **not** consumed here: the
  current production fragment-apply path (the `_apply_fragment`
  shim in `forge/sync/`) only forwards `middlewares`, not arbitrary
  `FragmentRenderer` tuples, so wiring PortSpec into a real fragment
  would require touching out-of-scope code. The three near-identical
  `inject.yaml` files for the Node + Rust port + adapter are the
  pragmatic v1; the PortSpec migration is a follow-up once `Fragment`
  grows a `renderers=()` field and the sync shim threads it through.
  Known limitations on Rust (mirroring the cache_port pattern):
  simultaneously enabling `llm_port` + `queue_port` / `cache_port`
  collides on `src/ports/mod.rs`, and `llm_openai` + `queue_apalis` /
  `cache_memory` / `cache_redis` collides on `src/adapters/mod.rs` —
  both declared via `conflicts_with` so the resolver fails loudly
  at plan-build time rather than silently corrupting the generated
  tree.

- **`PortSpec` — declarative port-wiring renderer (Pillar A.4,
  internal infra).** Second
  `forge.appliers.renderers.FragmentRenderer` implementation,
  joining `MiddlewareSpec` (Pillar A.2). Renders the per-backend
  port-wiring ceremony — port-interface import + adapter imports +
  factory wiring — at the canonical anchor for each language
  (`FORGE:APP_POST_CONFIGURE` in `container.py` for FastAPI,
  `FORGE:MIDDLEWARE_IMPORTS` in `src/app.ts` for Fastify,
  `FORGE:LIB_MOD_REGISTRATION` in `src/lib.rs` for Axum). Per-
  backend Jinja macros ship under
  `forge/templates/_shared/port_spec/{python,node,rust}.jinja`.
  Includes an advisory `detect_port_cycle` helper that flags
  `port_dependencies` cycles inside a single fragment's renderers
  tuple. **Internal infrastructure only — no plugin SDK exposure
  in this release.** First consumer (`llm_port` Node + Rust)
  lands in Pillar D.2; the goal there is "three `PortSpec`
  literals replace three near-identical `inject.yaml` files".

- **`cache_port` — generic K/V cache port + memory/redis adapters
  on all three backends (Pillar E.2).** New `CachePort` interface
  exposes `get(key) / set(key, value, ttl_seconds) /
  invalidate(key)` semantics with three tier-1 fragments:
  `cache_port` (the port itself), `cache_memory` (in-process LRU,
  zero external deps), and `cache_redis` (cross-replica safe,
  reuses the existing Redis sidecar on a dedicated db=3 to keep
  eviction independent from queue / rate-limit keysets). Selected
  via the new `reliability.cache: enum["none", "memory", "redis"]
  = "none"` option. Distinct from the existing
  `middleware.response_cache` HTTP middleware (which keys by
  request shape via fastapi-cache2) — the two are orthogonal and
  can coexist. Use cases the port targets: idempotency-key dedupe,
  LLM-response memoization, denormalized read caches. Python uses
  redis-py asyncio, Node uses ioredis, Rust uses the `redis` crate
  with its tokio async client. Known limitations (Pillar A.4
  PortSpec fix tracked): on Rust, simultaneously enabling
  `cache_port` + `queue_port` collides on `src/ports/mod.rs`, and
  enabling `cache_memory`/`cache_redis` + `queue_apalis` collides
  on `src/adapters/mod.rs` — both now declared via `conflicts_with`
  so capability resolution errors loudly at plan-build time.
  `reliability.cache=redis` does NOT auto-provision Redis on its
  own (the capability declaration is the dep marker, not the
  sidecar trigger); users need Redis provisioned elsewhere
  (auth.gatekeeper / queue.backend=redis / manual). Sibling
  cache_redis compose.yaml fragment is E.2.b follow-up.
- **`error_port` — RFC-007 envelope behind a swappable port
  (Pillar E.1).** Promotes the hand-written RFC-007 error-envelope
  code already shipping in every base template into a swappable port:
  one `serialize(exc) -> envelope` method on an `ErrorPort` Protocol
  (Python) / interface (Node) / trait (Rust). Default adapter
  `DefaultErrorPort` wraps the existing `app.core.errors` /
  `lib/errors.ts` / `crate::errors` machinery and keeps the wire
  shape byte-identical, so existing projects are unaffected.
  Tier 1 from the start (Python + Node + Rust) — the wire shape is
  already proven cross-language by the auth SDKs, so a Python-only
  port would be a downgrade. New option
  `observability.error_envelope` (BOOL, default `True`) gates the
  port; flipping it to `False` strips the adapter (full
  base-template strip lands as a follow-up). Enables plugins
  shipping custom envelopes to register their own adapter under the
  `ErrorPort` interface without forking the base template.
- **`ApplierRegistry` — pluggable per-suffix injector dispatch
  (Pillar A.1, SDK 1.2).** Replaces the hardcoded `if/elif` suffix
  chain at `forge/appliers/injection.py:_dispatch_injector` with a
  module-level registry at `forge/injectors/_registry.py`. Built-in
  suffixes (`.py` / `.pyi` → LibCST, TS family → regex/ts-morph,
  `*` wildcard → sentinel text fallback) seed at import time;
  plugins register new file types via the new
  `ForgeAPI.add_injector(suffix, injector)` SDK hook. Unblocks
  polyglot backend plugins shipping `.go` / `.kt` / `.rs` AST
  injectors without forking forge. SDK_VERSION bumps from `1.1` to
  `1.2` (see `docs/SDK_CHANGELOG.md` for the matching entry); the
  injector callable contract is the same positional
  `(file, feature_key, marker, snippet, position) -> None` shape
  every built-in injector already exposes.
- **Domain-emitter wiring in the codegen pipeline (RFC-010 /
  Pillar C.2, internal).** `forge/codegen/pipeline.py` grows a new
  `_emit_user_entities(config, project_root, collector)` pass that
  walks `<project_root>/domain/*.yaml` on every `forge new` /
  `forge --update` and dispatches the hardened domain emitters
  (PR #78) into per-backend output paths. Each entity produces, per
  backend in the project: a Pydantic v2 DTO + SQLAlchemy ORM model
  + alembic migration for every Python backend (ORM and migration
  skipped when `config.database_mode == "none"`), a Zod schema for
  every Node backend, and a serde + sqlx struct for every Rust
  backend. An OpenAPI component schema is always emitted under
  `<project_root>/openapi/<entity>.json` so frontend codegen and
  external tooling (Stainless, Speakeasy, openapi-generator) find
  every entity in one place. The `known_enums` set is sourced from
  the shipped shared registry (`forge/templates/_shared/domain/enums/*.yaml`)
  plus any project-local `<project_root>/domain/enums/*.yaml`, so
  specs referencing an undefined enum raise
  `UnknownEnumReferenceError` cleanly before any disk write. Every
  emitted block is wrapped in `FORGE:BEGIN domain_<entity>_<block>`
  / `FORGE:END domain_<entity>_<block>` sentinels (Python `#`,
  TS/Rust `//`) so future `zone="merge"` configurations flow
  through the existing three-way-merge applier under the same
  sentinel discipline the fragment injectors use; OpenAPI JSON is
  sentinel-free by design (no comment syntax). Provenance entries
  for domain emissions carry `template_name="_domain_emitter"` —
  a forward-compat metadata marker; today's harvester only buckets
  rows where `origin == "fragment"` and explicitly ignores
  base-template rows, so the marker is currently descriptive
  rather than functional. Full RFC-010 §"Generation pipeline"
  point 5 compliance (`origin="domain-emitter"` as a first-class
  literal that `--harvest` routes on) requires coordinated
  read-side updates in `forge/sync/{forge_to_project,project_to_forge}/`
  — deferred to a follow-up PR explicitly scoped to those
  cross-cutting sync sites. **`domain/` is optional** —
  backwards-compat for every existing 1.1.x project: missing or
  empty directory → no emit, no error.

- **Retry button on the chat RUN_ERROR banner (all 3 frontend
  stacks).** Pillar G.5 of the architectural improvement plan.
  When an agent run fails, the error banner now shows a `Retry`
  action next to `Dismiss` so users no longer have to retype the
  prompt. Implemented across Vue (`useAgentClient.retryLastRun()`
  / `useAiChat.retryLastRun()`), Svelte (`getAgentClient().retryLastRun()`
  surfaced via `getChatStore().retryLastRun()`), and Flutter
  (`ChatNotifier.retryLastRun()` exposed through `chatProvider`).
  The retry reuses the **same `threadId`** (conversation context
  preserved — distinct from `editAndResend`, which mints a fresh
  thread) and replays the original `forwardedProps` (model +
  approval mode + attachment IDs), so a transient backend failure
  on a multi-attachment turn doesn't drop the attachments. Snapshot
  of the last run options is captured on every `runAgent` call;
  cleared on `resetThread`. Vue + Svelte `retryLastRun()` guard
  against double-fire while a run is in flight; cross-stack
  `dismissError()` exposed as a public method on all 3 agent
  clients (UI no longer mutates error state directly).

- **`forge --plugins scaffold-fragment` (Pillar A.5, codegen-engine seam).**
  New CLI subcommand that renders a skeleton fragment tree for plugin
  authors — `fragments.py` registration stub, per-backend
  `inject.yaml` placeholders, per-backend `files/` stubs, and a
  `README.md` cheat sheet. Replaces the de facto "copy
  `examples/forge-plugin-example/` and rename everything" pattern
  with a first-class scaffold. Flags: `--name <id>` (required; must
  be a valid Python identifier — embedded in generated source),
  `--output-dir DIR` (defaults to
  `./plugins/forge-plugin-<name>/forge_plugin_<name>/fragments/<name>/`),
  `--backends python,node,rust` (subset opt-in), `--force` (overwrite
  non-empty targets). Generated `fragments.py` is import- and
  `ast.parse`-clean out of the box; every stub carries a visible
  `TODO:` marker. Jinja templates ship as package data under
  `forge/cli/scaffold/fragment_skeleton/` so wheel installs work
  without an editable checkout.

- **`FragmentRenderer` protocol (Pillar A.2, codegen-engine seam).**
  New `forge.appliers.renderers.FragmentRenderer` Protocol generalises
  the Epic K `MiddlewareSpec` dispatch so future declarative specs —
  RFC-009 `ServiceRegistrationSpec`, RFC-007 `ErrorCodeSpec`,
  `LifespanHookSpec`, and the Pillar D/E `PortSpec` — all flow through
  the same plan-build dispatch. `FragmentPlan.from_impl` gains a
  `renderers=` keyword that accepts a heterogeneous tuple of any
  `FragmentRenderer` and iterates them in deterministic
  `(order, name)` order, with per-renderer `attach_zone` honoured at
  emit time. `MiddlewareSpec` moves from `forge.middleware_spec` to
  `forge.specs.middleware` as the first protocol implementation; the
  legacy `from forge.middleware_spec import MiddlewareSpec` path keeps
  working via a one-line re-export shim (slated for removal in 2.0)
  so plugins shipped against Epic K continue importing unchanged. The
  legacy `middlewares=` keyword on `FragmentPlan.from_impl` /
  `FragmentPipeline.run` is preserved for one release and folded into
  `renderers=` transparently.

- **`PhaseHook` protocol + `ForgeAPI.add_hook` (Pillar A.3).** Plugins
  can now observe generator phases without forking `forge.generator`.
  Implementations supply three callbacks — `on_phase_start(name, ctx)`,
  `on_phase_end(name, ctx, duration_ms, error)`,
  `on_generate_complete(report)` — and register via
  `api.add_hook(MyHook())`. Hooks fire from the existing
  `phase_timer` contexts that already wrap every phase, so the only
  generator-side change is two lines in `phase_timer` itself plus a
  one-line `_fire_generate_complete` at the end of `generate()`. Hook
  exceptions are swallowed and logged inside the dispatcher — a buggy
  plugin cannot crash generation. Use cases: telemetry sinks, SBOM
  emitters, supply-chain signers, post-`forge new` shell scripts.
  Sibling Pillar A.1 PR also bumps SDK_VERSION to `1.2`; coordinated.
  New module `forge.hooks`; new test suite `tests/test_phase_hooks.py`;
  plugin-development guide gains a "Telemetry hook" section.

- **TypeSpec port contracts under
  `forge/templates/_shared/ports/{queue,object_store,llm,vector_store}/contract.tsp`
  (new).** Pillar D Phase 1 of the polyglot RFC-005 reduced-scope path —
  ships the four port contracts RFC-005 § "Port Contracts" enumerates as
  language-neutral TypeSpec spec files. Bodies follow the RFC verbatim
  where it provided one, with supporting `model` declarations added for
  types the RFC referenced by name only; per-contract comment headers
  document every Python-vs-RFC reconciliation. Plugin authors writing
  Node / Rust adapters outside the forge core now have a canonical
  spec to conform to. New opt-in `forge --ports-validate` verb at
  `forge/cli/commands/ports.py` compiles each contract via
  `npx -y @typespec/compiler` + the `@typespec/openapi3` emitter, prints
  per-port `VALID` / `INVALID`, exits 0 on success or 1 on any failure.
  Skips cleanly when `npx` is absent (node is not a hard forge
  dependency); CI that wants validation coverage must provision node
  alongside python. **Spec-only — no code is generated from these
  contracts today.** TypeSpec → bindings POC is plan Pillar D point 3,
  deferred to 1.4. Tests at `tests/test_typespec_contracts.py`.
- **`forge_canvas_core` (Dart pub.dev package, new).** Pillar B
  Phase 2B split of the existing `forge_canvas` package — extracts
  the framework-agnostic protocol surface into a sibling pure-Dart
  package under `packages/forge-canvas-core-dart/`. Mirrors the
  TypeScript Phase 2A pattern (`@forge/canvas-core` ↔ canvas-vue
  / canvas-svelte). Ships the `AgUiClient<E>` SSE client, the new
  `McpApprovalClient` + `McpApprovalRejected` (the wire-protocol
  bug fix for non-`auto` MCP tool invocations — mints an approval
  token via `POST /mcp/approval/mint` before calling
  `POST /mcp/invoke`), and a typed `McpBridge` interface
  (`mcpBridgeAvailable = false` on Dart by design — Flutter has no
  DOM iframe model). `forge_canvas` bumps to `1.0.0-alpha.7` and
  re-exports the full surface so existing
  `package:forge_canvas/...` import sites stay unchanged. The
  Flutter chat template rewrite that consumes `forge_canvas_core`
  directly is Pillar B Phase 3 — deferred until pub.dev publish.

### Internal

- **`packages/forge-canvas-core-dart/` scaffolding.**
  `pubspec.yaml` declares `forge_canvas_core` with pure-Dart
  `sdk: ">=3.0.0 <4.0.0"` (no Flutter dep), `analysis_options.yaml`
  uses `package:lints/recommended.yaml` (vs the Flutter-flavored
  `package:flutter_lints` the consumer package keeps), and the
  in-tree barrel `lib/forge_canvas_core.dart` exports the SSE,
  MCP-approval, and MCP-bridge surfaces. `forge_canvas` carries a
  `dependency_overrides: { forge_canvas_core: { path:
  ../forge-canvas-core-dart } }` block so local dev resolves
  without requiring `forge_canvas_core` to publish to pub.dev
  first. Stripped at publish time.
- **Follow-ups not in this PR (tracked):** the
  `publish-pub-dev-core` job in `.github/workflows/release.yml`
  (mirroring the existing `publish-pub-dev` job for
  `forge_canvas`) and the parallel dry-run leg in
  `.github/workflows/release-dryrun.yml`.

### Changed

- **`forge.domain.emitters` public functions accept `known_enums`.**
  All emit_* functions (`emit_pydantic`, `emit_sqlalchemy_model`,
  `emit_alembic_migration`, `emit_zod`, `emit_rust_struct`,
  `emit_openapi`, `emit_all`) grew a keyword-only
  `known_enums: Iterable[str] | None = None` parameter. The default
  preserves existing behaviour — existing callers that don't have an
  enum registry on hand keep working unchanged. Pass an explicit set
  to opt into `validate_enum_references` raising
  `UnknownEnumReferenceError` before emission when a spec references
  an enum that isn't declared. (Pillar C.1, internal hardening.)

### Documentation

- **Five ADR backfills.** Capture architectural decisions that exist
  in the codebase but were never written down. No code changes.
  - [`ADR-003`](docs/architecture-decisions/ADR-003-copier-over-cookiecutter.md)
    — why Copier (not Cookiecutter) as the template engine. Bidirectional
    sync (`--update`, `--harvest`, `--verify`) rests on `copier update`.
  - [`ADR-004`](docs/architecture-decisions/ADR-004-module-level-registries.md)
    — why module-level dict registries over a DI container. The five
    registries are static at runtime; a container's lifecycle machinery
    buys nothing.
  - [`ADR-006`](docs/architecture-decisions/ADR-006-forge-toml-provenance-manifest.md)
    — why `forge.toml` is the round-trip source of truth. Out-of-band
    manifest survives reformatters, IDE auto-edits, copy-paste, and
    binary files; per-file headers and git history do not.
  - [`ADR-007`](docs/architecture-decisions/ADR-007-separate-frontend-templates.md)
    — why three separate frontend templates instead of one with
    framework adapters. Sharing happens at the protocol layer
    (`@forge/canvas-core`, `forge_canvas_core`), not the framework layer.
  - [`ADR-008`](docs/architecture-decisions/ADR-008-codex-review-worktree.md)
    — why the `codex-toolkit` dispatcher runs Codex in a sibling git
    worktree. Stable snapshot, no risk of clobbering the user's WIP,
    reproducible reviews.

### Removed

- The `forge.feature_injector` deprecation shim. Migrate to
  `forge.sync.forge_to_project` for `apply_features` /
  `apply_project_features`; `forge.appliers.plan` for `_Injection`
  / `_load_injections` / `_render_snippet`; `forge.appliers.injection`
  for `_apply_zoned_injection` / `_dispatch_injector` and the rest of
  the zoned-injection runtime; `forge.fragments` for
  `_resolve_fragment_dir` / `FRAGMENTS_DIR`; `forge.injectors.sentinels`
  for marker primitives; `forge.appliers.files.copy_files` for file
  copy (the legacy `_copy_files` deprecation shim is also gone — use
  `copy_files(..., update_mode=...)` directly). See plan in
  `.claude/plans/happy-inventing-eclipse.md` Item 1.

### Added — weld-* SDK alignment (1.2.0-alpha.1)

Aligns forge templates with the platform's 2026-05 SDK restructure
(10 `weld-*` packages under `platform/sdks/`, Tier 0 → Tier 2 acyclic
DAG). See [`docs/architecture-decisions/ADR-005-weld-sdks.md`](docs/architecture-decisions/ADR-005-weld-sdks.md)
for the design rationale and [`UPGRADING.md`](UPGRADING.md) §"1.1 → 1.2 — weld-* SDKs"
for the migration playbook.

- **`__version__` bumped to `1.2.0-alpha.1`**.
- **Five new feature modules** (all Python-only, tier 3, all additive):
  - `events.bus` / `events.outbox` → `events_core`, `events_outbox`
    fragments. Scaffolds `src/app/events/` (bus factory + publish
    helper) and the alembic outbox migration via `weld-events`.
    `events_outbox` injects start/stop hooks for `OutboxRelay` into
    the lifespan via new `FORGE:LIFESPAN_STARTUP` / `FORGE:LIFESPAN_SHUTDOWN`
    markers in `core/lifecycle.py`.
  - `streaming.sse` → `streaming_sse` fragment. SSE endpoint at
    `/api/v1/stream` backed by `weld.streaming.CloudEventStreamer`.
    `depends_on=("events_core",)`.
  - `connectors.enabled` + `connectors.backends` (LIST) →
    `connectors_registry` fragment. Scaffolds `src/app/connectors/`
    with `build_default_connector_registry()`; reads the
    `connectors.backends` LIST option at render time so only selected
    `weld-connectors[<bk>]` extras land in the generated pyproject.
  - `airlock.client` → `airlock_client` fragment.
    `weld.airlock.AsyncAirlockClient` factory in DI with a
    lifespan shutdown hook.
  - `mcp_template.server` / `mcp_template.openapi_to_tools` →
    `mcp_template_server`, `mcp_template_openapi_tools` fragments.
    Hosts first-party MCP integrations via
    `weld.mcp_template.build_server()`; coexists with the
    consumer-side `platform.mcp` option (distinct namespace).
- **Python service template rewrite**: drops the in-tree `src/service/`
  shim (36 files, ~2000 LOC) and rewires `src/app/` onto direct
  `weld.*` imports. Import mapping is in `UPGRADING.md`. The base
  template no longer scaffolds the background-tasks endpoint or
  `TaskService`/`BackgroundTaskRunner` — services that need
  background work enable the existing `async.task_queue` option.
- **Three new copier prompts** on the Python service template:
  `sdk_consumption` (`monorepo` | `standalone` | `none`, default
  `monorepo`), `weld_base_sdks` (comma-separated, default covers
  the common base of every platform service), `service_path_prefix`
  (Traefik PathPrefix, default `/api/{{ project_slug }}`).
- **Multi-stage Dockerfile + compose fragment + entrypoint** across
  all three backend templates (Python, Node, Rust):
  - Builder copies weld-* SDK source from the `sdks` build context,
    builds wheels, strips `[tool.uv.sources]` from project pyproject
    so the runtime image resolves from `/wheels`.
  - Runtime stage runs as non-root `appuser` (uid 10001) with a
    urllib/curl healthcheck against `/api/v1/health/live`.
  - `docker-compose.fragment.yaml.jinja` is merged into the platform
    `docker-compose.yaml`: separate `<svc>-migrate` job + runtime
    service, Traefik labels with path-rewrite middleware, depends_on
    chain on postgres-healthy + keycloak-healthy.
  - `entrypoint.sh.jinja` runs migrations (`alembic upgrade head` /
    `prisma migrate deploy` / `/app/migrate`) before exec-ing the
    server. Advisory-lock serialized so concurrent replicas race
    safely.
- **Vue frontend template alignment** with `platform/apps/web`:
  adds `@hey-api/client-fetch`, `@tanstack/vue-virtual`, plus
  (under `include_chat`) `@microsoft/fetch-event-source` + `shiki`.
  New `include_sentry` copier prompt gates `@sentry/vue`. New
  `consumed_services` prompt drives a multi-spec
  `openapi-ts.config.ts` — each listed backend service gets its own
  `src/shared/api/generated/<svc>/` subfolder to avoid type
  collisions. Pinned `@hey-api/openapi-ts` to 0.56.3.

### Fixed — capability resolver crash on LIST options

`forge.capability_resolver._collect_fragments` raised `TypeError:
unhashable type: 'list'` when an option's value was a list, because
`spec.enables.get(value, ())` couldn't hash the list. Short-circuits
when `spec.enables` is empty (which is always the case for LIST /
STR / INT / OBJECT options — `_validate_enables_shape` forbids
`enables` on those types). Latent until the first LIST option
(`connectors.backends`) registered.

### Added — auth-stack rebuild

Replaces the legacy Keycloak-direct auth stack with the platform-auth model. See [`docs/auth-architecture.md`](docs/auth-architecture.md) for the architectural reference and [`UPGRADING.md`](UPGRADING.md) §"1.1 → 1.2 — auth-stack rebuild" for the migration playbook.

- **`auth.mode` Layer discriminator option** (`enum: generate | none`, default `generate`). Drives generation of the new auth namespace under `forge/features/auth/`. The legacy `include_auth=true` path is replaced; existing projects upgrade via `forge --migrate auth-keycloak-to-platform-auth` (Phase 10 codemod, scheduled to flip `auth.mode=generate`'s `enables` map alongside the legacy gatekeeper removal).
- **`forge/features/auth/` namespace — 11 new fragments**:
  - `platform_auth_sdk_python` — direct port of `platform/sdks/platform-auth/` (PyJWT-based, ES256, multi-issuer JWKSCache with stale-serve, `S2SClient` for client_credentials + RFC 8693 token-exchange, `MayActPolicy`, `IssuerTrustMap`, `RevocationStore`, scope matching with wildcards, test-token minter). 11 source modules + 11 unit-test modules ship at `<project>/sdks/platform-auth/`.
  - `platform_auth_sdk_node` — greenfield Node port (TypeScript, `jose`-based, ESM-native). Same public surface as Python. Fastify plugin (`platformAuthPlugin` via `fastify-plugin` for encapsulation-aware decoration; registers `onRequest` verifier hook + `app.requireScope(...)` decorator + RFC 7807 problem-response error mapping at `https://forge.dev/errors/<reason>`). Cross-language `reason()` slug contract pinned. Skip-list matches Python.
  - `platform_auth_sdk_rust` — greenfield Rust port (`jsonwebtoken`-based, `tokio` async, optional `axum` feature). Same public surface plus `From<jsonwebtoken::errors::Error>` / `From<reqwest::Error>` conversions. Test-token minter gated behind `testing` Cargo feature so production builds don't carry the `p256`/`ecdsa`/`rand` keypair-generation deps.
  - `platform_auth_gatekeeper` — Gatekeeper container as **sole token authority**. Mints ES256 internal JWTs from filesystem signing keys; serves them at `/auth/jwks`; issues S2S tokens at `/auth/token` (client_credentials + RFC 8693 token-exchange); BFF Redis sessions with two-key TTL atomicity (`gk:session:{id}:body` Fernet-encrypted, never extended; `gk:session:{id}:active` refreshed on every touch); `/auth/session` GET (read countdown) + POST (idle-extend, rate-limited 4/min). Forge's tenant claim flipped from `https://platform/tenant_id` to `https://forge/tenant_id`.
  - `platform_auth_gatekeeper_keygen` — one-shot init container that generates ECDSA P-256 signing keys before Gatekeeper boots. Declarative `compose.yaml` via `forge.services.fragment_compose`.
  - `platform_auth_python_middleware` — backend-scoped Python service-template wiring (6 modules: `auth_context.py` middleware, `core/auth.py` FastAPI dependency factories, `service/security/platform_auth_setup.py` AuthGuardBundle factory, `service/security/auth.py` token extraction shim, `service/client/auth.py` `ClientCredentialsAuth` cached OAuth2 token manager, `service/core/context.py` ContextVars).
  - `platform_auth_node_middleware` — backend-scoped Node service-template wiring (`bootstrapAuth(app)` reads env, registers `platformAuthPlugin`; `AuthenticatedRequest` type narrowing). `inject.yaml` adds the `import` + `await bootstrapAuth(app)` at the canonical FORGE markers.
  - `platform_auth_rust_middleware` — backend-scoped Rust service-template wiring (`init_auth()` startup function, `OnceLock<Arc<AuthGuard>>` shared state, `axum::middleware::from_fn`-compatible verifier with RFC 7807 problem-response output, `WWW-Authenticate: Bearer` on 401, same skip-list as Python and Node).
  - `platform_auth_session_timeout_vue` / `_svelte` / `_flutter` — frontend session-timeout composables implementing the BFF + inactivity-timeout SPA pattern: drift-immune countdown via `Date.now()` against `idleExpiresAt`, BroadcastChannel cross-tab leader election (web), debounced 30s `POST /auth/session` on `mousemove` / `keydown` / `scroll` / `visibilitychange`, visibility-gated, silent disable on 401 / timeouts=0. Pre-warning modal at `T - warn_at_seconds`.
- **`infra/keycloak-realm.json` additions** — `tenant-id` protocol mapper on both clients, `serviceAccountsEnabled: true` on the gatekeeper client, `tenant_id` attribute on the `dev@localhost` user. Additive; doesn't break the legacy gatekeeper.
- **Cross-SDK parity contract** — `forge/tests/contract/auth_sdk_parity/` ships the canonical scenario spec (20 frozen-dataclass scenarios covering happy paths, alg/kid rejection, expiry, audience mismatch, multi-audience accept, issuer trust + tenant suspension, missing/non-UUID tenant claim, jti revocation, RFC 8693 act chain, custom claim names, claim-name-mismatch rejection) plus 15 meta-invariants gating the spec's internal coherence. Python / Node / Rust parity orchestrators each run the full spec against their SDK. Caught: the first cross-SDK API drift (Node + Rust `StaticMayActPolicy` rekeyed to Python's canonical `audience → actors` keying); three cross-SDK testing-helper drifts (Rust `role_claim` → `roles_claim`, Node `roleClaim` → `rolesClaim`, Python missing `roles_claim`/`scope_claim` configurability — all aligned to plural cross-language convention).
- **Rust SDK end-to-end Tower-stack test** — `tests/integration_axum.rs` exercises `AuthLayer` + `RequireScope` + the `IdentityContext` `FromRequestParts` extractor against a real Axum router via `tower::ServiceExt::oneshot`. 6 cases: identity binding through to handlers, missing-bearer 401 with RFC 7807 problem-response, missing-scope 403, multi-scope AND, skip-list bypass, wildcard scope satisfaction. Gated on both `axum` + `testing` features so consumers of the bare verifier don't pay the Tower compile cost. Wired into the parity orchestrator's `cargo test` runner.
- **Per-decision audit callback (Rust SDK)** — `audit.rs` ships `AuthAuditRecord` (cross-language record shape: `decision`, `audience`, `audiences`, `ts_unix`, `tenant_id`, `tenant_slug`, `subject`, `actor`, sorted `scopes`, `jti`, `iss`, `reason`), `AuthDecision` enum with `as_str()` returning the `"allow"`/`"deny"` slug pinned across SDKs, `AuthAuditCallback` type alias (`Arc<dyn Fn(...) + Send + Sync>`). `AuthGuardConfig.audit` field + emit-on-allow path matches Python `_emit_audit` and Node `_emitAudit` semantics — the deny path is currently a no-op for cross-SDK parity (forward-compat extension point all three SDKs flip together). Symmetric cross-SDK behavioural tests: 5 in Rust (`tests/audit_callback.rs`), 5 in Python (`tests/unit/test_auth_guard.py::TestAudit`), 4 in Node (`test/audit_callback.test.ts`). Each suite covers the allow-path record shape, deny-path no-op, and `tenant_slug` propagation. Symmetric structural invariants pin the record-shape contract in all three SDK invariant suites.
- **Optional `tenant_slug` cross-SDK rollout** — `IdentityContext.tenant_slug` (Python `tenant_slug: str | None`, Node `tenantSlug: string | null`, Rust `tenant_slug: Option<String>`) populated from the configured `tenant_slug_claim` JWT claim (default `https://forge/tenant_slug`). Surfaces via the verifier's `verify` and propagates into `AuthAuditRecord` on the allow path. Absent / non-string claim → `None`/`null`; informational field only — never causes token rejection. Locked in by parity scenario `happy_with_tenant_slug` (mints a slug, asserts all 3 SDKs surface it identically) and Rust integration test `audit_callback_propagates_tenant_slug`.
- **`Fragment.target_frontends` field** + applier gating — frontend-targeted fragments (e.g., `platform_auth_session_timeout_{vue,svelte,flutter}`) declare `target_frontends=(FrontendFramework.X,)` so `apply_project_features` skips them when the project's frontend doesn't match. Closes a real bug where Python-backend projects with no frontend (or the wrong frontend) had three frameworks' session-timeout files dumped at the project root. Empty tuple = "applies regardless" (preserves existing behavior for every other fragment). Wired through `generator.py`; the updater path stays compatible (frontend gating is no-op when `frontend_framework=None`).
- **Discriminator-fanout `_is_user_selected` fix** — `forge/capability_resolver.py` now treats an option with multi-fragment `enables` (e.g., `auth.mode=generate` → `[sdk_python, sdk_node, sdk_rust, ...]`) as a fanout/bundle: per-language fragments with no compatible backend silent-skip instead of hard-erroring. Single-fragment options preserve the hard-error path (catches genuine user typos). Fixed 6 latent test failures in `tests/test_updater.py` / `tests/test_plan_update.py` / `tests/test_plugin_e2e.py` that surfaced once `auth.mode=generate`'s default got stamped into `forge.toml`. Behavior locked in by `test_discriminator_fanout_skips_unmatched_languages_silently`.
- **`docs/auth-architecture.md` new** — architectural reference: TL;DR, four roles of Gatekeeper, per-language SDK design parity table, three token-flow walkthroughs (browser login, S2S on-behalf-of, inactivity-driven session refresh), 16-entry configuration-knobs table, compliance notes (SOC 2 / ISO 27001 / NIST 800-63B-4 / OWASP / RFC 8693 / RFC 9068), Cutover-status section explicitly enumerating the Wave 1 (shipped: 6 of 11 fragments, SDKs + frontend session-timeout) vs Wave 2 (pending: backend middleware + gatekeeper compose cutover) split, migration overview, out-of-scope follow-ups (step-up auth, IETF SSE/CAEP, risk-adaptive timeouts, concurrent-session limits, alternative `KEY_BACKEND`s, shared SPA SDK).
- **Flutter native auth refresh** — `SessionTimeoutService.forNative(...)` factory closes the previously-deferred mobile gap. Native (iOS / Android) clients can't use the BFF cookie model; the new factory takes a `RefreshAccessToken` callback (wired to `KeycloakAuthService.refreshAccessToken`) that rotates the access token via `flutter_appauth.token(refreshToken: ...)` on debounced activity. Countdown is computed locally from the access-token expiry, capped at the configured idle-timeout window so a long-lived access token can't widen the SOC 2 / ISO 27001 / NIST 800-63B-4 compliance window beyond intent. On idle / absolute timeout (or refresh-token rejection), the service invokes a consumer-supplied `onForcedLogout` callback. The base flutter-frontend-template's `auth_interceptor.dart` gains a single-retry path on 401 (refresh-then-replay) following the standard mobile-OIDC pattern. Web clients keep the cookie-based BFF flow unchanged. 12 new structural invariants in `tests/test_features_auth_session_timeout_flutter.py` pin the new factory + callback wiring + base-template integration points.

### Behavioural changes engineers should know

1. **`/auth` does NOT extend the session.** Every authenticated route passes through Traefik's ForwardAuth, but the call is read-only — no idle-TTL touch. Sessions extend exclusively on real user-interaction events (mouse / keyboard / scroll / visibility), not background API traffic. Compliance posture (SOC 2 / ISO 27001 / NIST 800-63B-4) requires this.
2. **Internal JWT TTL is 5 minutes.** A token revoked upstream stays verifiable for up to 5 minutes after `/logout`. Features with hard revocation requirements gate on the session itself, not the bearer.
3. **Scope-based authz is now first-class.** Endpoints declare `requireScope("things:read")` (or the language equivalent) and AuthGuard rejects 403 with a typed `ScopeRequired` error.
4. **S2S calls go through `S2SClient`.** Handles client_credentials and RFC 8693 token-exchange automatically with cached tokens.
5. **`SameSite=Lax` cookie + JSON-only mutation enforcement.** CSRF is mitigated at the API layer via `Content-Type: application/json` + CORS preflight. Audit non-JSON mutating endpoints before upgrading.

### Architectural improvement plan — landed (1.2.0-draft)

The full plan + per-initiative reviews live at
`.claude/plans/role-expertise-you-melodic-pixel.md` and
`~/.codex/runs/20260520T131313Z-119433.final.md`. Below is the per-RFC-002
breaking / changed / added summary for callers who pin contracts.

**Breaking — typed adapter contracts (Initiative #1)**

- `forge/appliers/plan.py::_Injection.position` / `.zone` are now
  `Literal` types instead of bare `str`. Construction with a literal
  outside `("after", "before")` / `("generated", "user", "merge")`
  raises `FragmentError` immediately. Production callers always
  passed string literals; this only bites code that built `_Injection`
  from untrusted dynamic strings.
- `forge/extractors/pipeline.py::CandidatePatch.kind` / `.risk` are
  now `Literal`s. New error codes
  `CANDIDATE_PATCH_BAD_KIND` / `CANDIDATE_PATCH_BAD_RISK` fire on bad
  values from bundle deserializers or plugin extractors.
- `forge/sync/project_to_forge/apply_bundle/_dispatch.py` replaced
  the if/elif `kind` ladder with a `dict[CandidateKind, ApplyHandler]`
  registry. `ApplyHandler` is a `Protocol` with the typed keyword-only
  signature; handlers registered with the wrong shape fail at type
  check time instead of first call.

**Breaking — plugin extractor retention (Initiative #1 sub-task 4)**

- `ForgeAPI.add_extractor(kind, extractor, *, fragment=None)` now
  retains the extractor instance on
  `PluginRegistration.extractor_registrations` (was `del`'d before).
  Global overrides (`fragment=None`) replace the built-in extractor
  of matching kind at harvest time. Fragment-scoped overrides
  (`fragment="name"`) are retained but explicitly NOT yet invoked —
  the harvester emits a one-shot `plugin.extractor.scoped_skipped`
  warning per process. Plugin authors targeting fragment scope should
  treat their registration as a no-op until per-fragment pipeline
  plumbing lands.

**Breaking — Dart canvas prop typed shapes (Initiative #8)**

- Generated Dart props for nested-object arrays now emit `final
  class` types instead of `List<Map<String, dynamic>>`:
  - `DynamicFormProps.fields: List<DynamicFormField>`
  - `DataTableProps.columns: List<DataTableColumn>`
  - `WorkflowDiagramProps.nodes: List<WorkflowDiagramNode>`
  - `WorkflowDiagramProps.edges: List<WorkflowDiagramEdge>`
  (`DataTableProps.rows` stays `List<Map<String, dynamic>>` per the
  schema's `additionalProperties: true`.)
- Hand-written prop interface mirrors deleted from the Vue, Svelte,
  and Dart canvas package indexes; the generated module is now the
  only source of truth. `TestGeneratedPropsOnly` greps the components
  to prevent reintroduction. Wire format unchanged — only Dart code
  that constructed these props directly needs updating. See
  `packages/forge-canvas-dart/CHANGELOG.md` for the Dart-specific
  bump under `1.0.0-alpha.6`.

**Breaking — manifest schema v3 → v4 (Initiative #3)**

- `forge.toml` schema bumps to v4 with a new `[forge.frontend]`
  table carrying `framework` + `app_dir`. V3 manifests are read
  through an inference fallback that scans on-disk `apps/` subdirs;
  the migration is automatic on first re-stamp. Callers that pin the
  manifest schema version must update.

**Changed — agent event discriminator (Initiative #4)**

- The seven AG-UI event variants emitted by `forge/codegen/event_union.py`
  use a `{kind, payload}` wrapped envelope across Pydantic (Python),
  Dart (`AgUiEvent.parse`), and TypeScript canvas-vue / canvas-svelte.
  The forge-generated `agent_streaming` `/ws/agent` endpoint emits
  its own simpler streaming protocol (`text_delta` / `tool_call_*` /
  etc.) that is NOT in the AG-UI vocabulary — the canvas-vue /
  canvas-svelte `AgUiClient` docstrings now clarify the scope to
  prevent a confused integration. The backend `AgentEvent` Pydantic
  model carries `kind` alongside the legacy `type` field for one
  release (validator rejects mismatched values to surface the
  transition explicitly).
- Vue + Svelte canvas packages ship a new `AgUiClient` shim mirroring
  the Dart shape, for callers integrating with AG-UI-compliant
  external servers.

**Changed — option-metadata compatibility (Initiative #7)**

- `forge/options/_registry.py::Option` gains new optional fields:
  `requires_database`, `requires_backend`, `allowed_backends`,
  `allowed_frontends`, `incompatible_with`. `ProjectConfig.validate()`
  now walks `OPTION_REGISTRY` checking these flags instead of
  hardcoding feature-name dispatch. Error messages from
  `_validate_database_mode` may have slightly different ordering
  (sorted by registry path rather than insertion order); error codes
  unchanged.
- `forge/config/typed_config.py::TypedConfig.aliases` now reads from
  the registry's `OPTION_ALIAS_INDEX` (single source of truth) —
  previously duplicated.
- Added `requires_database=True` on `agent.mode`, `rag.embeddings`,
  `rag.reranker`, `async.rag_ingest_queue` to catch transitively
  DB-backed combinations the old in-line validator missed.

**Added — agent-grade reports (Initiative #5)**

- New `forge.reports` module ships `GenerationReport`,
  `UpdateReport`, `SyncPlanReport` dataclasses with versioned
  envelopes (`_report_version: int`).
- `forge --json` now emits a `GenerationReport` alongside the legacy
  thin payload — effective config, option origins, fragment graph,
  file inventory, provenance sidecar paths, warnings, skipped
  toolchains, next-action hints, and `hidden_mutations` (e.g. the
  silent `auth.mode` coercion at `cli/builder.py`).
- `forge --update --json` emits an `UpdateReport` with per-file
  disposition counts. `SyncPlanReport` ships as a tested scaffold —
  no production caller wires it yet; that's a clean follow-up.
- Plugin command failures now wrap via `_json_error()` when `--json`
  is set (was empty-stdout-crash before).

**Added — frontend-only sync (Initiative #3)**

- `forge --update` and `forge --harvest` now work on projects with
  `backend.mode=none`. The updater synthesizes a bridge backend for
  the per-fragment apply loop and strips it before restamping.
- The updater threads `frontend_framework` into update-time feature
  application so `Fragment.target_frontends` gating is effective.

**Added — plugin emitter execution (Initiative #2, scoped)**

- `ForgeAPI.add_option` now routes through `register_option()`,
  picking up alias-index updates and collision detection for free.
- `ForgeAPI.add_emitter` retains the callable on
  `PluginRegistration.emitter_registrations`.
  `forge.codegen.pipeline.run_codegen` walks `LOADED_PLUGINS` after
  the built-in passes and invokes each plugin's emitter with
  `(project_root, config, resolved)`. Last-loaded wins on target
  collision via a two-pass collect+invoke (the loser does NOT fire).
  Failed emitters are isolated per plugin and logged via
  `plugin.emitter.failed`.
- Deferred from the full Initiative #2: plugin backends / frontends
  end-to-end (template-dir dispatch, dependency dispatch for plugin
  backends, CLI parser plugin-command discovery). The hooks are
  registered; the consumers aren't yet wired.

**Performance — cache hot paths (Initiative #6, scoped)**

- Per-`forge --update` / `--harvest` invocation cache for
  `forge.toml` keyed on `(path, mtime)`. A fragment with N merge
  blocks now pays one tomlkit parse instead of N.
- Process-global `mtime`-keyed cache for `*.schema.json` reads in
  `forge.codegen.ui_protocol` + `forge.codegen.canvas_contract`
  (transitively covers `canvas_props` + `event_union` loaders).
  Returns deep-copies on hit so callers can't poison each other.
- `forge.codegen.pipeline._write` skips disk writes when content
  sha256 matches the existing file. Provenance is still recorded so
  the re-stamp downstream stays consistent.
- Deferred: parallel execution graph from `ResolvedPlan`; `ts-morph`
  daemon refactor; LibCST parse-once in `python_ast`.

**Tooling — tests toward behavior (Initiative #9)**

- Lane D in `tests/matrix/runner.py` no longer reports `success` on
  empty candidate lists. Scenarios that legitimately produce zero
  candidates must opt-in via `expect_candidates: false` in
  `scenarios.yaml`.
- Lane D's live-tree mutate-restore replaced with a tempdir copy of
  the `forge/` package plus a subprocess invocation. No more race
  when CI runs Lane D in parallel.
- New `tests/test_canvas_decoder_contract.py` feeds a shared JSON
  corpus through the Pydantic backend serializer + Dart
  `AgUiEvent.parse` + TS `getEventKind` shim and asserts byte-
  identical decoding.
- Matrix runner now emits `::warning::` GitHub Actions annotations
  per skipped scenario, naming the missing dependency. Silent
  docker / npm / cargo skips no longer masquerade as green.
- Fuzz strategies expanded to LIST / OBJECT / multi-backend /
  frontend-swap.
- E2E skip rationales refreshed for Svelte + Flutter chat with the
  blocker docs linked.

**SDK note** — `forge.api.SDK_VERSION` remains `"1.1"`; the API
surface gained provisional extension points (`add_extractor`,
`add_emitter`) but no breaking changes to existing stable methods.
`docs/SDK_CHANGELOG.md` carries the per-method notes.

---

## [Unreleased — 1.1 wave]

> First wave of the 12-month post-1.0 roadmap (see `plans/role-expertise-you-sprightly-nova.md`). Foundation epics that unblock the rest: structured error hierarchy (D), registry freeze + symmetry audit (I), FragmentContext plumbing (E), feature_injector decomposition (A), MiddlewareSpec abstraction (K), coverage gate (S), ty pin + canary (X), release dry-run workflow (Z). The architectural-review plan (`plans/role-expertise-you-lexical-sutherland.md`) lands its P0 + P1 items here: P0.1 file-level merge + Epic A finish, P0.2 plugin e2e gate, P1.1 options package split, P1.2 plan-update + remove-fragment verbs, P1.3 declarative compose snippets, P1.4 ts-morph instrumentation.

### Added — P2 opportunistic items (1.1.0-alpha.2)

These ship as opportunistic follow-ons to the P0 + P1 work in the
architectural-review plan. Each is small, additive, and unblocks a
specific operator / plugin-author pain point.

- **Fragment cycle path diagnostics** — `_FragmentRegistry._audit_no_cycles` now walks the dependency graph with a coloured DFS on cycle detection and reports the actual path (`a → b → c → a`) instead of just the unsorted set of fragments involved. `FragmentError.context` carries both the legacy `cycle_among` set and the new `cycle_path` list. 4 new tests in `tests/test_fragment_registry_freeze.py` lock in the diagnostic shape (two-node cycle, three-node cycle, DAG-isolation, legacy-context-preservation).
- **`forge --plan --graph`** — emits a Mermaid `graph TD` diagram showing every option that contributed to the resolved plan (with the chosen value), every fragment with its target backends, the option-→-fragment edges (`enables`), and the fragment-→-fragment depends_on edges (dotted-line variant). Pipe to `mermaid-cli`, paste into a GitHub Markdown ` ```mermaid ` fence, or render at mermaid.live. Closes the "why is fragment X applied?" support loop. 4 new tests in `tests/test_plan_and_dryrun.py` cover the wire format, option-node inclusion, depends_on-edge inclusion, empty-plan handling.
- **`forge --log-json` + `--log-level`** — top-level CLI overrides for `FORGE_LOG_FORMAT` / `FORGE_LOG_LEVEL`. `--log-json` emits NDJSON to stderr (one JSON object per line, complete with `ts` / `level` / `logger` / `event` / structured fields); `--log-level={DEBUG,INFO,WARNING,ERROR}` overrides the env-var default. A pre-parse scan of `sys.argv` ensures even plugin-load events captured before the full argparse pass honour the flags. 3 new tests in `tests/test_forge_logging.py` cover explicit-fmt-overrides-env, explicit-level-overrides-env, NDJSON line-shape contract.
- **ADR / RFC distinction documented in `CONTRIBUTING.md`** — clarifies that `docs/architecture-decisions/` covers decisions about the *shape forge generates* while `docs/rfcs/` covers decisions about *forge itself*. Two doc trees on purpose; the README in each was previously the only signal of the split.
- **Plugin author quickstart** in `docs/guides/plugins.md` — 10-minute scaffold pointing at `examples/forge-plugin-example/` plus a "common gotchas the P0.2 CI gate catches" list. Each gotcha references the specific drift mode (Fragment-without-category, relative fragment_dir, files/-not-mirroring-backend-root, compose.yaml-location, missing-package-data) so the next plugin author hits zero of them.
- **Performance regression bench tests** at `tests/test_perf_merge_hot_paths.py`, `bench` pytest marker. Lightweight `time.perf_counter`-based budgets for the merge-mode hot paths — `file_three_way_decide` (~0.1 µs/op, budget 10 µs), `sha256_of_file` (~75 µs/op for 1 KiB, budget 5 ms), `is_binary_file` (~80 µs/op, budget 1 ms), `plan_update` walk (budget 30 s). No new dependencies (no `pytest-benchmark`); opt-in via `pytest -m bench`. Establishes a measurement floor; CI gating is a future ratchet move once historical data stabilises across machines.
- **Long / deep-path robustness tests** at `tests/test_long_path_update.py` exercise the merge-mode hot paths over a 6-level nested directory tree under `tmp_path`. Catches general path-handling bugs across platforms; an opt-in stress test (skipped by default) deliberately crosses Windows' 260-char `MAX_PATH` for verifying long-path support on machines with `LongPathsEnabled=1`. 5 active tests + 1 opt-in.
- **Canvas-contract test depth** — 18 new tests in `tests/test_canvas_contract.py` cover the previously-uncovered branches in `forge.codegen.canvas_contract`: missing/non-string `component_name`, missing/non-dict `props`, every `_check_type` branch (string / integer-rejects-bool / integer-rejects-string / number-rejects-bool / number-accepts-int+float / boolean / object-rejects-array / object-accepts-dict / enum-mismatch / enum-listed), and the `cli_lint` CLI handler (clean / dirty / unparseable JSON exit codes 0 / 1 / 2). `forge/codegen/canvas_contract.py` coverage 71.6% → 98%.
- **Coverage floors ratcheted** to reflect the new measured baselines. `merge` 85→92, `updater` 78→83, `capability_resolver` 95→96; `plan_update` (new module) gets its own floor at 83%/target 90%. Other modules unchanged. Updater and feature_injector both dipped briefly when the new merge-mode code paths landed; backfilled tests for `_copy_files` deprecation shim, mode-specific user-modified summary output, the disabled-fragment uninstall log path, and the resolve-failure → OptionsError re-raise (8 new tests in `tests/test_updater.py` + `tests/test_feature_injector.py`) brought both back above their floors. `tests/test_coverage_gates.py` and `docs/coverage-policy.md` updated together — the gate's docs-stay-in-sync test enforces the consistency.

### Added — P1.3 (declarative docker-compose snippets in fragments)

- **`forge/services/fragment_compose.py`** — loader + registrar for `<fragment>/compose.yaml`. Fragments can ship a YAML that the generator parses into a `ServiceTemplate` and registers under `SERVICE_REGISTRY` keyed by capability. Schema mirrors `ServiceTemplate` field-for-field (image, command, environment, ports, volumes, named_volumes, healthcheck, depends_on, networks, restart, extra). Top-level `capability:` keys the registry; `service:` block holds the docker-compose service definition.
- **`forge.services.fragment_compose.fragment_roots_from_plan`** — derives the fragment-root directory for each `ResolvedFragment` (one level above the per-language sub-dirs); fed into `register_fragment_services` to register every plan fragment's sidecar.
- **`forge.generator.generate`** — calls `register_fragment_services(fragment_roots_from_plan(plan.ordered))` before `render_compose` so fragment-declared sidecars land in the rendered docker-compose.yml alongside the existing imperative services. Additive: the existing `{% if X %}` blocks in `forge/templates/deploy/docker-compose.yml.j2` keep working; fragments adopt declarative compose at their own pace.
- **`FragmentComposeError`** — distinct from `FragmentError`; raised on malformed YAML, missing `capability` / `service` keys, or non-string `service.name` / `service.image`. Surfaces the offending file path so the operator can fix the YAML.
- **14 new tests** in `tests/test_fragment_compose.py` cover minimal + full schema parsing, every required-field error, dedup of repeated roots, idempotent re-registration with identical templates, and conflict-detection on differing templates.

### Added — P1.4 (ts-morph default instrumentation)

- **`forge.doctor.check_ts_morph_toolchain`** — surfaces whether `FORGE_TS_AST=1` AST-based TypeScript injection is reachable. Probes Node on PATH, executes `node -e "require('ts-morph')"` to verify the npm package resolves, checks the bundled helper script, and reports the env-var state. Status is `ok` when reachable (showing whether the env var is currently active), `warn` with an actionable fix otherwise. Wired into `forge --doctor` as the `ts-morph:toolchain` row.
- **`docs/operations/troubleshooting.md`** — new section "TypeScript injection: regex anchors vs ts-morph AST" documenting the opt-in path, the toolchain requirements, the doctor surface, and the planned default flip.
- **5 new tests** in `tests/test_doctor.py` cover Node-missing, helper-missing, ts-morph-not-reachable, and reachable-with-/-without-env-var paths.
- **No code change** to the regex/ts-morph dispatch yet — instrumentation only this quarter, per the plan; the default flip is a stable-1.x conversation gated on telemetry.

### Added — P1.2 (`forge --plan-update` + `--remove-fragment`)

- **`forge.plan_update.plan_update`** — dry-run preview producing a structured `UpdatePlanReport` with per-file decisions (`applied` / `new` / `skipped-idempotent` / `skipped-no-change` / `no-baseline` / `conflict` / `error`) plus the list of fragments the next `--update` would uninstall. Reuses `file_three_way_decide` from P0.1 but doesn't write or emit sidecars. Mode-aware: `--mode merge|skip|overwrite|strict` colours the per-file `reason` field.
- **`UpdatePlanReport.as_dict`** — JSON-friendly view used by `forge --plan-update --json`. Includes a `summary` block with `applied`, `conflicts`, `total` counts so downstream tooling can branch on a clean re-apply vs. one with unresolved conflicts.
- **`forge --plan-update`** + **`forge --remove-fragment NAME`** CLI verbs. The first prints the dry-run report. The second finds the option whose `enables` map references the named fragment, flips that option to its default in `forge.toml`, then runs `update_project` so the existing Epic-F uninstaller cleans up. Errors out when zero or multiple options enable the fragment (`conversation_persistence`, for example, is enabled by ~four options — the user has to disable each explicitly).
- **`forge.cli.commands.plan_update`** + **`forge.cli.commands.remove_fragment`** — handlers; both honour `--quiet` and `--json` and inherit the `--mode` flag from the existing update parser.
- **12 new tests** in `tests/test_plan_update.py` and `tests/test_remove_fragment.py` cover refused-without-manifest, clean-project no-conflict, forced-conflict via baseline drift, all four mode dispatches, JSON round-trip, single/zero/multiple-enabling-option lookup.

### Added — P1.1 (split `forge/options.py` into a package)

- **`forge/options/`** package — refactor of the 1581-line `forge/options.py` monolith into one module per dotted-path namespace. Public surface (`Option`, `OptionType`, `FeatureCategory`, `OPTION_REGISTRY`, `OPTION_ALIAS_INDEX`, `register_option`, `resolve_alias`, `to_json_schema`, `ordered_options`, `options_by_namespace`, `get_option`, `CATEGORY_*`, `Stability`, `ObjectFieldSpec`) is re-exported from `forge.options.__init__`; every `from forge.options import X` import-site keeps working unchanged.
- **Modules:** `_registry` (types + registry + helpers), `_schema` (JSON Schema emitter), and one per namespace: `middleware`, `observability`, `async_work` (`async.*` + `queue.*`), `conversation`, `agent` (`agent.*` + `llm.*`), `chat`, `rag`, `platform_ops` (`platform.*` + `object_store.*` + `security.*`), `reliability`, `layers` (`backend.*` + `frontend.*` + `database.*`).
- **No behaviour change.** Every existing test passes. Future option additions land in a 100–250-line module instead of merging against a 1500+ line file.

### Added — P0.2 (plugin end-to-end CI gate)

- **`.github/workflows/plugin-e2e.yml`** — installs `examples/forge-plugin-example` against the working tree on every PR touching `forge/api.py`, `forge/plugins.py`, the option/fragment registries, the example, the workflow itself, or `pyproject.toml`. Runs `pytest -m plugin_e2e` against the live registry — discovery, registration, CLI list/schema, generation with the option enabled, update flow.
- **`tests/test_plugin_e2e.py`** + **`plugin_e2e` pytest marker** — 11 tests covering the discovery → registration → CLI introspection (`--plugins list` text + JSON, `--list --format json`, `--schema`) → generation (fragment files land + injection applies + provenance recorded) → update (idempotent re-apply) flow. Skips cleanly on environments where the example plugin isn't installed.
- **`forge/api.py`** — explicit "Stable Public API" annotation block in the module docstring with a per-symbol Since / Compatibility table. `add_option` / `add_fragment` / `add_backend` / `add_frontend` / `add_command` / `add_service` / `PluginRegistration` are stable; `add_emitter` is provisional.
- **Reference plugin fixed** — `examples/forge-plugin-example/` had three pre-P0.2 drift bugs the new gate caught: (1) `Fragment(category=...)` and `Fragment(summary=...)` kwargs no longer exist; (2) `inject.yaml` referenced the non-existent `FORGE:STARTUP_HOOKS` marker on `src/app/main.py`, should have been `FORGE:LIFECYCLE_STARTUP` on `src/app/core/lifecycle.py`; (3) the fragment file was at `files/hello.py` (lands at backend root, not importable) instead of `files/src/app/hello.py` (lands at `src/app/hello.py`, importable as `app.hello`). All fixed; `pyproject.toml` now declares `package-data` so wheel installs ship the fragment tree.
- **`docs/guides/plugins.md`** — new "Stable API surface" section pointing at `forge/api.py`'s table + the CI gate, plus an updated Project structure layout reflecting the corrected fragment file paths and the absolute-vs-relative `fragment_dir` rule for plugins.

### Added — P0.1 (file-level three-way merge + Epic A finish)

### Added — P0.1 (file-level three-way merge + Epic A finish)

- **`forge.merge.file_three_way_decide`** — pure decision function over content hashes for `forge.appliers.files`. Mirrors `three_way_decide` for inline blocks; adds `applied` / `skipped-idempotent` / `skipped-no-change` / `no-baseline` / `conflict` action vocabulary covering the 7-row decision table. New helpers `sha256_of_file` (CRLF-normalising for text, raw bytes for binary), `is_binary_file` (null-byte heuristic on the first 8 KiB, matching Git), and `write_file_sidecar` (text → `<target>.forge-merge` with comment banner; binary → `<target>.forge-merge.bin` with no banner).
- **`forge.appliers.files.copy_files`** — Epic A finish. Body moved out of `forge.feature_injector._copy_files` into the applier module that owns it. Replaces the legacy `skip_existing: bool` parameter with `update_mode: Literal["strict","merge","skip","overwrite"]`. `_copy_files` remains as a deprecation shim translating the boolean to the enum; scheduled removal 2.0.
- **`forge.fragment_context.UpdateMode`** — new `Literal` exported from `fragment_context`. `FragmentContext.skip_existing_files: bool` is replaced by `FragmentContext.update_mode: UpdateMode` plus a `file_baselines: Mapping[str, str]` field that the applier reads for the merge decision. `FragmentContext.filtered()` accepts both as kwargs.
- **`apply_features` / `apply_project_features`** — replace `skip_existing_files: bool` with `update_mode: UpdateMode = "strict"`. Add `file_baselines: Mapping[str, str] | None = None`. Default stays "strict" so fresh generation continues to raise on fragment overlap.
- **`forge --update` defaults to `--mode merge`** — three-way decides every file-copy collision against the manifest baseline. The deferral comment ("Three-zone merge lands in 1.0.0a3") is gone. CLI flag `--mode={merge,skip,overwrite}` lets callers opt out: `skip` reproduces pre-1.1 behaviour, `overwrite` clobbers user edits.
- **`forge --update` summary** gains `update_mode` (the mode actually run) and `file_conflicts` (count of `.forge-merge` / `.forge-merge.bin` sidecars present after the apply pass) so callers can branch on a clean re-apply vs. a conflict-heavy one without parsing log output.
- **Updater seeds the new collector with every prior provenance record** (was: user-origin only). Files the applier legitimately skips — `skipped-no-change`, `no-baseline`, mode=skip — keep their prior baselines in the re-stamped manifest. Pre-P0.1, those baselines silently disappeared and every `--update` re-baselined from scratch.
- **`FILE_MERGE_CONFLICT` error code** in `forge.errors`. Distinct from the existing `MERGE_CONFLICT` (injection-block) so logs and downstream consumers can branch on the kind. Reserved for a future `--strict-conflicts` mode that escalates sidecars to a non-zero exit; not raised by the default merge path (which mirrors injection-zone behaviour: emit sidecar, continue).
- **`forge migrate-adopt-baseline` codemod** under `forge/migrations/migrate_adopt_baseline.py`. Walks `services/`, `apps/`, `tests/` and stamps current SHAs into `[forge.provenance]` with `origin="base-template"` so pre-P0.1 projects can adopt their tree as the merge baseline. Idempotent, dry-run aware, doesn't clobber existing records, skips `node_modules` / `.venv` / build dirs / forge-internal state. Strictly opt-in.
- **47 new tests** across `tests/test_file_three_way_merge.py` (27 — the 7-row decision table + binary detection + sidecar shape), `tests/test_updater.py` (8 integration tests covering merge/skip/overwrite mode behaviour, the no-baseline path, the forced-conflict scenario via baseline drift, and the user-deleted re-emit path), `tests/test_migrations.py` (6 covering adopt-baseline stamping, skip-list, idempotency, dry-run, no-manifest), and updates to `tests/test_feature_injector.py` (cover the three new copy_files modes), `tests/test_fragment_context.py` (the new field shape).

### Added — Epic F (provenance-driven uninstall)

- **`forge/uninstaller.py`** — ``uninstall_fragment(project_root, fragment_name, provenance_tbl, collector, removed_blocks_in_files=...)`` computes per-file classification for every provenance record tagged to a disabled fragment and acts on it: **unchanged files deleted**, **user-modified files preserved** with a warning, **missing files pruned from the manifest** silently. Empty parent directories left behind get pruned too.
- **Sentinel-block scrubber** — ``_remove_sentinel_block(file, feature_key, marker)`` removes a ``FORGE:BEGIN``/``FORGE:END`` pair from a file the fragment doesn't own outright. Returns ``"removed"`` on clean scrub, ``"missing"`` when the block isn't there, ``"conflicted"`` on orphan/duplicate/nested pairs (file untouched, user resolves).
- **`disabled_fragments(previous_provenance, current_plan_fragments)`** — set-diff helper identifying which fragments were previously present but aren't in the current resolved plan.
- **`forge --update` integration** — between the sentinel audit and the re-apply pass, computes disabled fragments from the previous provenance + current plan, runs ``uninstall_fragment`` per disabled. Summary payload gains an ``uninstalled`` key with per-fragment ``deleted/preserved/missing/removed_blocks/conflicted_blocks`` lists. Console output surfaces counts when not in quiet mode.
- **Escape hatch** — ``[forge.update].no_uninstall = true`` in ``forge.toml`` reverts to the pre-Epic-F behaviour (disabled fragments leave their files on disk). Read on every update; corrupt-manifest returns ``False``.
- **`MergeBlockCollector.parse_key`** — inverse of the canonical ``key_for``. Epic F walks ``[forge.merge_blocks]`` to derive ``(rel_path, feature_key, marker)`` triples for the disabled fragment's injections so sentinel-bounded blocks get scrubbed too, not just the fragment's ``files/`` outputs.
- **24 new tests** in `tests/test_uninstaller.py` cover disabled-fragment set-diff, unchanged/user-modified/missing file paths, fragment isolation (other fragments' files untouched), collector record pruning, sentinel block clean/missing/conflicted paths, ``UninstallOutcome`` serialisation, forge.toml no_uninstall flag with corrupt-manifest fallback, and ``MergeBlockCollector.key_for``/``parse_key`` round-trip.

### Added — Epic U (mutmut weekly workflow + breaking-change PR gate)

- **`.github/workflows/mutmut.yml`** — scheduled Monday 06:00 UTC full run on the critical-path modules, `workflow_dispatch` for ad-hoc re-runs, PR gate that fires on `breaking-change`-labelled PRs (skips if no critical-path file changed). Uploads `mutmut-results-*.txt` artefacts with 30-day retention.
- **`tests/mutmut_baselines.json`** — per-file kill-rate floors + survivor caps for `feature_injector`, `merge`, `provenance`, `updater`, `injectors/python_ast`, `injectors/ts_ast`. Ratchet policy mirrors coverage gates.
- **`.github/workflows/scripts/mutmut_enforce.py`** — post-run enforcer parses `mutmut results`, aggregates survivors per source file, compares against the baseline caps, fails the job with an actionable message when a module regresses. Skips silently when no mutmut cache exists (keeps the workflow valid before the first run).

### Added — Epic AA (full-feature golden snapshot preset)

- **`full_feature` preset in `tests/test_golden_snapshots.py`** — 5th golden preset exercising observability + reliability middleware + conversational-AI persistence + agent tools + chat attachments + webhooks + CLI extensions + AGENTS.md, plus a Vue frontend with auth + chat + OpenAPI. Generates ~3,600 files (vs. ~600 for `python_minimal`). Catches regressions in the "rich" Python-stack union that the 4 minimal presets miss.
- **Expanded snapshot-noise filter** — `.git/` at any depth (frontend templates run their own `git init`) + `__pycache__/` + `.pyc` files (frontend post-generate scripts byte-compile their modules). Without these filters, the frontend-inclusive preset was nondeterministic across runs. Keeps the four pre-Epic-AA presets byte-identical to their prior snapshots.
- **`ProjectConfig.options` threaded through the test's config-copy** so the preset's rich options actually reach the generator (pre-AA, the copy dropped `options=`, which wouldn't have mattered until the first preset that set anything — this one).

### Added — Epic CC (troubleshooting + known-issues docs)

- **`docs/operations/troubleshooting.md`** — 12 entries covering install/CLI (uv PATH setup, ty alpha quirks), generation (Windows long paths, Flutter SDK, Keycloak ports, hung prompts), `forge --update` (Epic H lock + sentinel audit errors, Epic G alias deprecation warnings), Docker + runtime, and packaging + release (Epic DD contaminants, Epic Z release-dryrun gate).
- **`docs/reference/limitations.md`** — tracked limitations grouped by template/platform/polyglot/tooling. Each row has impact + workaround + tracking reference so contributors stop rediscovering the same gotchas.
- **README's Support section links both docs** so they're discoverable from the project front page.

### Added — Epic RFC-Q (polyglot ports roadmap — design only)

- **`docs/rfcs/RFC-005-polyglot-ports.md`** — design document specifying TypeSpec port contracts for `queue`, `object_store`, `llm`, `vector_store` cross-language, enumerating the adapter inventory (16 new adapters × 3-5 engineer-days each = ~13 engineer-weeks), and **deferring implementation to 2.x** so 1.1.x can focus on Python depth + Epic J ops parity.
- The narrower follow-up — publishing the TypeSpec spec files without adapters so plugin authors have something to conform to — is tracked as a P2 1.x item.
- `docs/rfcs/README.md` index updated with the RFC-005 row.

### Added — Epic H (updater lock + sentinel integrity audit)

- **`forge/updater_lock.py`** — ``acquire_lock(project_root, no_lock=False)`` context manager writes ``<project_root>/.forge/lock`` with PID + ISO-8601 timestamp on entry, removes on exit. Stale locks (owning PID no longer alive) are reclaimed transparently. Cross-platform liveness via ``os.kill(pid, 0)`` on POSIX and ``OpenProcess`` via ctypes on Windows — stdlib only, no psutil. Raises ``ProvenanceError(PROVENANCE_UPDATE_LOCK_HELD)`` when a live process already holds the lock. ``no_lock=True`` is a no-op escape hatch for read-only filesystems.
- **`forge/sentinel_audit.py`** — structural auditor for ``FORGE:BEGIN`` / ``FORGE:END`` pairs. Detects six issue kinds: ``orphan-begin``, ``orphan-end``, ``duplicate-begin``, ``duplicate-end``, ``nested-pair``, ``end-before-begin``. `audit_file`/`audit_targets` scan; `raise_if_corrupt` raises ``InjectionError(INJECTION_SENTINEL_CORRUPT)`` with file+tag+line details of the first 10 issues.
- **`forge --update` wraps the whole apply pass in ``acquire_lock`` + runs the sentinel audit pre-apply** so broken hand-edits surface with a pointed error before the injector runs (pre-Epic-H, they silently double-injected). The `no_lock` kwarg propagates through `update_project()` for tests + read-only-fs scenarios.
- **18 new tests** across `tests/test_updater_lock.py` — lock create/cleanup/exception paths, stale-PID reclaim, live-PID rejection, `no_lock=True` escape hatch, corrupt-lock resilience; sentinel audit clean/orphan-begin/end-before-begin/duplicate/nested-pair cases; `audit_targets` aggregation; `raise_if_corrupt` raises with the right code.

### Added — Epic O (FrontendLayout registry)

- **`forge/frontends.py`** with ``FrontendLayout(framework, ui_protocol_path, ui_protocol_emitter, canvas_manifest_path, shared_enums_dir, shared_enums_emitter)`` frozen dataclass and ``FRONTEND_LAYOUTS`` registry. Vue/Svelte/Flutter layouts registered at import time matching the pre-Epic-O paths byte-for-byte.
- **`codegen/pipeline.py` rewritten to loop over the registered layout** rather than carrying three hardcoded per-framework ``if/elif`` ladders. Adding a plugin-defined frontend is now "register a FrontendLayout"; the codegen pipeline picks it up without editing.
- **`register_frontend_layout(layout)` + `get_frontend_layout(framework)`** public API for plugins.
- **7 new tests** in `tests/test_frontend_layouts.py` assert every built-in framework has a registered layout whose paths match the pre-Epic-O hardcoded values, duplicate-registration rejection, plugin layout swap.

### Added — Epic DD (package integrity gate)

- **`tests/test_package_integrity.py`** — builds sdist + wheel via `uv build`, asserts every sentinel template file (one per backend, one per frontend, docker-compose, a cross-cutting fragment) is present in both artefacts, and no cache/build contamination (`.ruff_cache/`, `.mypy_cache/`, `.pytest_cache/`, `node_modules/`, `.dart_tool/`, `.venv/`, `.pyc`, `.DS_Store`, etc.) leaked into either archive. The wheel test also verifies the `forge = forge.cli:main` entry point is registered.
- **Whitelist self-check** asserts every file under `forge/templates/` uses a known extension — a new file type (e.g. `.lua`) fails the test and forces an explicit decision about whether to add it to `KNOWN_TEMPLATE_EXTENSIONS` or to exclude it from templates.
- **`package-integrity` CI job** (`ubuntu-latest` × Python 3.13). Runs `pytest -m package_integrity`. Own job because the `uv build` costs ~30s; keeping it out of the main `test` matrix preserves wall-clock. Main `test` + `coverage` jobs now use `-m "not e2e and not package_integrity"` to mirror the carve-out.
- **`package_integrity` pytest marker** registered in `pyproject.toml`.

### Fixed — Epic DD

- **`MANIFEST.in`** now excludes `.ruff_cache/`, `.pytest_cache/`, `node_modules/`, `.venv/`, `htmlcov/`, `.next/`, `.svelte-kit/`, `dist/`, `build/`, `.dart_tool/`, `.DS_Store`, in addition to the pre-existing `__pycache__/` + `.mypy_cache/` exclusions. Previously these would ship in the sdist if they accumulated in a contributor's working tree.
- **`[tool.setuptools.exclude-package-data]`** applies the same exclusion list to the wheel's `forge.templates` package-data include so the wheel also stays clean.
- **Removed `forge/templates/infra/gatekeeper/src/app.log`** — stray dev log from running gatekeeper locally; not git-tracked, but would have been picked up by `uv build` on any contributor's machine where it exists.

### Added — Epic G (option aliases + rename-options codemod)

- **`Option.aliases: tuple[str, ...] = ()`** and **`Option.deprecated_since: str | None`** — an Option can declare deprecated paths that previously pointed at it. Aliases pass the same path-shape validation as the canonical path and cannot collide with any registered Option's path or alias.
- **`OPTION_ALIAS_INDEX: dict[str, str]`** + **`resolve_alias(path)`** — populated at registration, looked up by the resolver to rewrite user-supplied alias paths to canonical before validation.
- **`capability_resolver._apply_option_defaults` transparently rewrites aliases** with a `WARNING`-level log pointing at the `forge migrate-rename-options` codemod. If a user sets both the alias and the canonical path, the resolver raises `OptionsError(OPTIONS_UNKNOWN_PATH, context={alias, canonical})` so the conflict surfaces explicitly rather than silently dropping one.
- **`forge migrate-rename-options` codemod** (`forge/migrations/migrate_rename_options.py`) — parses `forge.toml`'s `[forge.options]` table via tomlkit (comment-preserving), rewrites aliased keys to canonical paths, saves. Idempotent; re-running after a successful rewrite returns `skipped_reason="No aliased option keys found in forge.toml"`. Registered in `discover_migrations()` so `forge migrate` + `forge migrate-rename-options` both find it.
- **20 new tests** in `tests/test_option_aliases.py` cover `Option.__post_init__` alias validation (self-conflict, duplicates, invalid path, `deprecated_since` without aliases), `register_option` collision detection (alias vs canonical, alias vs alias, canonical vs alias), resolver rewrites + deprecation warning, resolver raises on alias+canonical conflict, and the full codemod lifecycle (apply, dry-run, idempotent re-run, canonical-already-set short-circuit, missing manifest, missing options table, `discover_migrations` registration).

Unblocks Epic L's `forge migrate-auth-provider` codemod, which will rename existing option paths that gate Keycloak-specific behaviour (still Python-only but due to be renamed once auth becomes a first-class Option).

### Added — Epic K (MiddlewareSpec cross-language abstraction)

- **`forge/middleware_spec.py`** — frozen `MiddlewareSpec(name, backend, order, import_snippet, register_snippet, rust_mod_snippet=None)` dataclass. One spec == one middleware registration for one backend; a fragment supporting Python+Node+Rust ships three specs. Three per-backend renderers (`render_fastapi_middleware`, `render_fastify_plugin`, `render_axum_layer`) emit the `_Injection` records that the existing inject.yaml pipeline would have written by hand. `render_middleware_injections()` dispatches by `spec.backend` and sorts deterministically by `(order, name)`.
- **`Fragment.middlewares: tuple[MiddlewareSpec, ...] = ()`** — per-fragment middleware declarations. Default empty, so existing fragments are unaffected until they migrate.
- **`FragmentPlan.from_impl` + `FragmentPipeline.run`** gained `middlewares` + `backend` parameters. At plan time, specs matching the current backend are expanded into synthesised `_Injection` records and appended after any inject.yaml entries. Backward-compatible — callers that don't pass `middlewares` behave exactly as before.
- **`correlation_id/python` migrated** as the first proof: `inject.yaml` deleted, replaced by a single `MiddlewareSpec` literal on the Fragment. The generated project sees byte-identical output. Epic J (Node+Rust ops parity) migrates the remaining five middleware fragments (rate_limit, security_headers, observability, observability_otel, response_cache) and backfills Node+Rust specs for the Python-only ones.
- **13 new tests** in `tests/test_middleware_spec.py` — per-renderer shape, dispatch + filtering + ordering semantics, FragmentPlan integration (middleware-only fragment, absent-backend short-circuit, inject.yaml+middlewares combined), and a pair of sanity checks asserting the migrated `correlation_id` fragment has the expected MiddlewareSpec and that its legacy inject.yaml is gone.

### Added — Epic A (fragment applier decomposition)

- **`forge/appliers/` subpackage** — four single-responsibility applier classes each consuming a `FragmentContext` + `FragmentPlan`:
  - `FragmentPlan(fragment_dir, files_dir, injections, dependencies, env_vars, feature_key)` frozen dataclass; `FragmentPlan.from_impl(impl, feature_key, options=...)` is the resolution pass.
  - `FragmentFileApplier` — copies the fragment's `files/` tree.
  - `FragmentInjectionApplier` — runs `inject.yaml` entries through the zoned dispatcher.
  - `FragmentDepsApplier` — merges dependencies into `pyproject.toml` / `package.json` / `Cargo.toml`.
  - `FragmentEnvApplier` — appends env vars to `.env.example`.
- **`FragmentPipeline`** composes them in the canonical order (files → injection → deps → env). `FragmentPipeline.default()` is the factory; the pipeline is a frozen dataclass so plugins + downstream epics can swap a single applier without restating the rest (Epic K substitutes `FragmentInjectionApplier`).
- **`_apply_fragment` shrinks** from ~50 LOC orchestrator to 5 lines that dispatch into `FragmentPipeline.default().run(ctx, impl, feature_key)`. This is Phase 1 of the decomposition — the applier bodies still live in `feature_injector.py` and the appliers call through as thin wrappers, so behaviour is preserved byte-for-byte. A follow-up moves the helper bodies into the applier modules.
- **11 new tests** in `tests/appliers/` cover `FragmentPlan.from_impl` resolution (missing dir, files-only, inject-only, deps + env propagation, Jinja render) and `FragmentPipeline` ordering + short-circuit semantics on empty plan segments.

### Added — Epic E (FragmentContext + option_values plumbing)

- **`forge/fragment_context.py`** with a frozen `FragmentContext` dataclass bundling `backend_config`, `backend_dir`, `project_root`, filtered `options`, `provenance`, `skip_existing_files`. This is the sole input to `_apply_fragment` (Epic A's applier decomposition builds on it).
- **`FragmentContext.filtered()`** classmethod — builds a context where `options` is a read-only view restricted to the paths the impl declared in `FragmentImplSpec.reads_options`. A fragment cannot peek at option values it didn't declare.
- **`FragmentImplSpec.reads_options: tuple[str, ...] = ()`** — per-impl declaration of which option paths this fragment consumes at apply time. Most fragments leave it empty (pre-Epic-E behaviour).
- **`capability_resolver._validate_reads_options()`** pass — runs before `_topo_sort`, asserts every `reads_options` path resolves against `OPTION_REGISTRY`. Orphan paths surface as `OptionsError(OPTIONS_UNKNOWN_PATH, context={fragment, path})` at resolve time rather than silently missing at apply time.
- **`inject.yaml render: true`** flag — when set on an injection entry, the snippet is Jinja-rendered with `options={...}` in scope before injection. `StrictUndefined` semantics — a typo raises `FragmentError` with a hint to declare the path in `reads_options`.
- **`apply_features` / `apply_project_features` kwargs**: new optional `option_values` and `project_root` (backward-compatible — callers that haven't threaded the plan through yet keep working). `forge.generator.generate` and `forge.updater.update_project` pass both.
- **15 new tests** in `tests/test_fragment_context.py` cover direct + filtered construction, frozen-dataclass immutability, resolver validation of `reads_options`, Jinja rendering with `options` in scope, and end-to-end threading through `apply_features` via a captured context.

### Added — Epic I (FRAGMENT_REGISTRY freeze + startup audit)

- **`_FragmentRegistry` subclass of `dict`** — replaces the bare `dict[str, Fragment]` backing `forge.fragments.FRAGMENT_REGISTRY`. Adds a one-shot `freeze()` method and a `frozen: bool` flag. Before `freeze()` behaves like an ordinary dict; after `freeze()`, `__setitem__` / `__delitem__` raise `PluginError(PLUGIN_REGISTRY_FROZEN)` so late registrations don't silently slip past the audit.
- **Startup audit** runs four passes over the full registry:
  1. *Orphan `depends_on`* — hard error, surfacing a silently-renamed fragment at startup instead of mid-generation.
  2. *Orphan `conflicts_with`* — logs a warning (the "if this ever gets added, we conflict" pattern is legitimate).
  3. *Conflict symmetry* — warns when fragment A declares a conflict with B but B doesn't reciprocate. A future epic will tighten to a hard error with automatic promotion.
  4. *Cycle detection* — Kahn toposort dry-run; a cycle raises `FragmentError(cycle_among=[...])` with the affected fragment names.
- **`Fragment.__post_init__`** rejects self-consistency violations at construction: a fragment listing itself in `conflicts_with`, or the same name appearing in both `depends_on` and `conflicts_with`.
- **`plugins.load_all()` calls `FRAGMENT_REGISTRY.freeze()`** after the per-plugin loop. A failed audit captures into `FAILED_PLUGINS` as `<registry audit>` rather than crashing, so introspection verbs (`forge --list`, `forge --plugins`) still work for diagnosis.
- **17 new tests** in `tests/test_fragment_registry_freeze.py` cover every audit pass, freeze/thaw semantics, `Fragment.__post_init__`, and a sanity check that the shipped built-in registry audit passes.
- **`plugins.reset_for_tests()`** now also thaws the registry so the test-wide fixture that clears plugin state works in both orders.

### Added — Epic Z (release dry-run rehearsal + 72h preflight)

- **`.github/workflows/release-dryrun.yml`** (`workflow_dispatch`-only) — rehearses every publish path against dry-run endpoints / offline validators: Python sdist+wheel build + `twine check`, `forge` CLI install smoke from the built wheel (`forge --version / --help / --list`), `npm publish --dry-run` for canvas-vue and canvas-svelte, `flutter pub publish --dry-run` + `flutter analyze` for forge_canvas, CHANGELOG section extraction. On green, writes a `release-dryrun/ok` check-run on the rehearsed SHA.
- **`preflight-dryrun` job in `release.yml`** — queries the GitHub Checks API for `release-dryrun/ok` on the tagged commit, fails fast unless a successful rehearsal completed within 72h. Every publish-* job now depends on this preflight, so a broken build or misconfigured registry never reaches PyPI / npm / pub.dev.
- **Escape hatch** — repo variable `SKIP_DRYRUN_GATE=true` bypasses the preflight with a CI warning. Mutations are in the repo log, so an accidental lingering bypass is visible in audit.
- **`RELEASING.md` "Pre-release dry-run protocol"** section — step-by-step protocol, per-job failure taxonomy, escape-hatch usage.

### Added — Epic X (ty pin + upstream regression canary)

- **ty pinned to exact version** (`0.0.29`) in `pyproject.toml`. The old `>=0.0.1a7` constraint let uv resolve to whatever Astral had released — handy for bug fixes, dangerous for CI stability. New ty bumps now go through `.github/workflows/ty-upgrade.yml` (scheduled monthly + `workflow_dispatch`), which runs the canary + forge typecheck before opening a PR with the lock churn.
- **`tests/fixtures/ty_canary/`** — 12 small Python files exercising the 5 enabled ty rules (`unresolved-import`, `invalid-argument-type`, `invalid-return-type`, `missing-argument`, `unknown-argument`) plus `TypedDict` and generic `Protocol` inference smoke tests. Each file has a `# expect: error[<rule>]` or `# expect: ok` header.
- **`tests/test_ty_canary.py`** — 13 tests that subprocess-invoke `ty check` against each fixture and assert the reported diagnostic rule matches the header. A divergence means ty's behaviour changed — the CI failure surfaces as "upstream ty regression" rather than "forge typing regression".
- **Split `typecheck` job in `ci.yml`** into `typecheck-forge` and `typecheck-ty-canary`. Running in parallel. A green `typecheck-ty-canary` with a failing `typecheck-forge` points at forge code; the reverse points at ty.
- **`.github/workflows/ty-upgrade.yml`** — scheduled bot. First Monday of each month (or `workflow_dispatch`) bumps ty to the latest alpha, runs canary + forge typecheck, opens a labelled PR with the bump. Humans review + merge.

### Fixed — Epic X (pre-existing ty diagnostics)

- Seven typechecker diagnostics that ty 0.0.29 surfaces on the forge codebase (the old `>=0.0.1a7` constraint was resolving to a newer ty in practice, but the diagnostics weren't being enforced — CI was running whatever uv cached). Each is suppressed with a targeted `# ty:ignore[<rule>]` comment and a rationale. Sites: `forge/cli/interactive.py:106` (BackendLanguage + plugin-sentinel union), `forge/codegen/enums.py:104, 109` (narrowed dict.get), `forge/domain/typespec.py:111` (optional-import-shim), `forge/generator.py:19, 20, 217` (Windows stdout reconfigure + provenance origin assignment). These are intentional pragmatic suppressions; a future epic tightens the relevant dataclass types to remove them.

### Fixed — Epic X (pre-existing ruff diagnostics)

- Ruff 0.15.11 (refreshed via `uv sync` during Epic X) surfaces ~37 pre-existing UP037 / UP035 diagnostics — quoted type annotations that `from __future__ import annotations` makes unnecessary, plus `typing.Callable` → `collections.abc.Callable` migrations. All auto-fixed by `ruff check --fix`; the resulting diffs are mechanical.
- Three style fixes that ruff flags but can't auto-fix: SIM108 ternary in `forge/domain/emitters.py` (ternary applied); SIM102 nested-if in `forge/feature_injector.py` (combined with `and`); UP042 `str + Enum` in `forge/domain/spec.py` (kept with a targeted `noqa` — StrEnum changes `==` semantics and the YAML loader depends on the current behaviour).
- `uv.lock` refreshed for the new ty pin + the current ruff.

### Added — Epic S (per-module coverage gates)

- **`tests/test_coverage_gates.py`** — per-module coverage floors for the critical path (`capability_resolver`, `feature_injector`, `merge`, `provenance`, both AST injectors, `updater`). Parametrized across the gate table so a CI failure names the exact module that regressed instead of just "coverage low".
- **`coverage` job in `.github/workflows/ci.yml`** — ubuntu × Python 3.13 only; runs pytest with `--cov-report=xml,json`, re-runs the per-module gate against the fresh report, uploads XML to Codecov (OIDC for the public repo, `CODECOV_TOKEN` honoured if set).
- **`docs/coverage-policy.md`** — documents the two-layer gate (project-wide `fail_under` + per-module floors), the ratchet policy, and how to add or lower a floor with RFC-002 justification.

### Changed — Epic S

- Measured baseline at the start of Epic S was 83.5% project-wide — well above the existing `fail_under = 75`. The exploration assumption of 26% was stale. Project-wide floor stays at 75 as the coarse safety net; the new per-module floors lock in the actual 80–97% critical-path coverage with a 2pp safety margin so small incidental drops don't block unrelated PRs.
- `.gitignore` now ignores `coverage.json` and `coverage.xml` so the CI-generated reports don't accidentally land on a feature branch.

### Breaking

### Breaking

- **Epic D — Error hierarchy.** `GeneratorError` is now an alias for the new `ForgeError` base class; six typed subclasses (`OptionsError`, `FragmentError`, `InjectionError`, `MergeError`, `ProvenanceError`, `PluginError`) replace the single flat error type. `except GeneratorError:` keeps catching every forge failure so call sites outside the project are unaffected. Assertions that compare `type(err).__name__ == "GeneratorError"` or rely on exact-type equality break — migrate to `isinstance(err, ForgeError)` or the specific subclass. No codemod; the search-and-replace is too coupled to the local test style to mechanise safely. See `UPGRADING.md`.

### Added

- **`forge.errors.ForgeError`** with fields `message`, `code`, `hint`, `context` and an `as_envelope()` helper the CLI uses for the `--json` error envelope.
- **Machine-readable error codes** (`OPTIONS_UNKNOWN_PATH`, `FRAGMENT_DIR_MISSING`, `INJECTION_ANCHOR_NOT_FOUND`, `MERGE_CONFLICT`, `PROVENANCE_MANIFEST_MISSING`, `PLUGIN_COLLISION`, etc.) exported from `forge.errors` so plugins + tests can branch on `err.code` without regex-matching message strings.
- **Subclass-aware exit codes** in the CLI: injection failures exit 3, merge 4, provenance 5, plugin 6; options/fragment/generator stay at 2. 0 remains success.
- **`--json` envelope extension.** The error envelope now includes `code`, `hint` (when present), and `context` (when non-empty). The legacy `{"error": "<message>"}` shape is preserved for config-parse errors that surface as raw `ValueError` / `KeyError`.
- **`tests/test_errors.py`** — 18 new tests covering the hierarchy, alias identity, envelope serialisation, and exit-code mapping.

### Changed

- Every `raise GeneratorError(...)` in the core control plane (`capability_resolver`, `feature_injector`, `injectors/python_ast`, `injectors/ts_ast`, `updater`, `api`) now raises the most specific subclass with a named `code`. Generator-orchestration raises in `forge/generator.py` continue to raise via the `GeneratorError` alias (= `ForgeError`) — they'll migrate to a future `GenerationError` subclass in a follow-up.
- Bare `ValueError` / `FileNotFoundError` raises in the AST injectors are promoted to `InjectionError` with codes (`INJECTION_TARGET_MISSING`, `INJECTION_ANCHOR_NOT_FOUND`, `INJECTION_ANCHOR_AMBIGUOUS`, `INJECTION_MARKER_MISSING`) so CLI + tests see a uniform error surface.

---

## [1.0.0] - 2026-04-20

> **forge 1.0 — stable.** First stable release of the clean-break 1.0 series. Every capability shipped across the five alphas (a1–a5) + beta (b1) + release candidate (rc1) is now under the normal SemVer contract: breaking changes require a major bump, minor bumps add features, patches fix bugs. See `RELEASING.md` for the post-1.0 deprecation policy.

### Summary of the 1.0 series

- **Schema-first core** — single YAML or TypeSpec source drives every language's domain models + OpenAPI + enums.
- **AST-aware injection** — LibCST for Python, regex (or ts-morph subprocess) for TypeScript, with three-zone (generated / user / merge) semantics and `.forge-merge` conflict sidecars.
- **Provenance manifest** — `forge.toml` records SHA-256 + origin per file; `forge --update` classifies user-modified vs unchanged vs fragment-modified.
- **Ports-and-adapters** — vector store (6 providers), LLM (4 providers), queue (2 providers), object store (2 providers) — swappable via env, no regeneration.
- **Plugin SDK** — third parties ship `forge-plugin-*` packages that add options, fragments, backends, frontends, commands, or emitters via entry points.
- **Canvas packages** — `@forge/canvas-vue`, `@forge/canvas-svelte`, `forge_canvas` publish 5 canvas components + AG-UI streaming client + runtime prop lint.
- **Real MCP integration** — stdio JSON-RPC subprocess client, tool discovery + invoke endpoints, HMAC-signed approval tokens, append-only audit log.
- **30 options × 51 fragments × 662 tests** — full coverage across the registry.

### Version

- `pyproject.toml` version `1.0.0`.
- Canvas packages held at `1.0.0-alpha.5` until the first post-1.0 bump (their own lifecycle; see RFC-003).

### Post-1.0 policy

- Breaking changes require a major bump and a deprecation cycle (one minor release warning → removal in the next).
- Dropping a Python / Node / Rust / Flutter version is breaking; backport fixes to the last minor that supported it for one year.
- RFC-002's codemod contract remains in force — every breaking change ships with a `forge migrate-<name>` codemod or a documented manual step.

---

## [1.0.0rc1] - 2026-04-20

> First release candidate. Feature-frozen since 1.0.0b1 with no new changes — this RC exists to exercise the full release-rehearsal pipeline (RFC-004) against every registry (TestPyPI, npm `alpha` tag, pub.dev `--dry-run`) before the v1.0.0 tag push. Promoted to stable 1.0.0 if the rehearsal completes without rollbacks.

### Changed

- `pyproject.toml` version pinned to the RC identifier (no `.dev0` suffix) so `uv build` emits installable `forge-1.0.0rc1` artefacts for the rehearsal.

### Tests

662 passing, 1 skipped (no change from b1).

---

## [1.0.0b1] - 2026-04-20

> First beta — **feature freeze**. No new features accepted from this point; only bug fixes, doc polish, and release-rehearsal hardening.

### Added

- **Expanded CI matrix** (`.github/workflows/e2e.yml`): forge's own tests run on Python 3.11 / 3.12 / 3.13; generated-project suites (python-vue, node-svelte, rust-none, multi-backend-keycloak) run on every template-touching PR; canvas-packages build + typecheck for `@forge/canvas-vue`, `@forge/canvas-svelte`, `forge_canvas` on every commit.
- **`docs/ARCHITECTURE.md`** — rewritten to document the post-1.0 design: registry triad, generation pipeline, injector backends, provenance + merge blocks, codegen layer, canvas packages, plugin architecture, testing surface.
- **`docs/guides/getting-started.md`** — 10-minute tour from install to a running project, including headless YAML invocation, incremental-change verbs, codemods, and agent-friendly stdin/json patterns.
- **`docs/rfcs/RFC-004-release-rehearsal.md`** — pre-1.0 rehearsal policy, dry-run mechanics, lockstep version check, rollback asymmetries per registry.
- **`release.yml` dry-run trigger** — `workflow_dispatch` inputs `dry_run` (default true) and `target` so maintainers can rehearse the full PyPI + npm + pub.dev pipeline without tagging.

### Changed

- **README visuals** — placeholder image slots removed; replaced with a real Mermaid architecture diagram + links to `ARCHITECTURE.md` and `GETTING_STARTED.md`.

### Tests

662 passing, 1 skipped (no change from a5 — feature freeze).

---

## [1.0.0a5] - 2026-04-20

> Fifth alpha — **feature-complete**. Lifts the remaining 4 canvas components (CodeViewer, DataTable, DynamicForm, WorkflowDiagram) into the published `@forge/canvas-vue`, `@forge/canvas-svelte`, and `forge_canvas` packages. Ships beta-gate testing infrastructure: 4 golden-snapshot presets (python/node/rust/multi-backend), OpenAPI contract tests for the domain DSL, and mutation testing scoped to the fragment-injector critical path.

### Added

- **4 canvas components extracted** across all three frameworks (A5-1 through A5-4):
  - `CodeViewer` — syntax-highlighted code with filename header + line numbers. highlight.js (web) / flutter_highlight (mobile).
  - `DataTable` — sortable columns + client-side pagination.
  - `DynamicForm` — JSON-schema-driven form dispatching by field type (text/number/password/email/select/checkbox/textarea).
  - `WorkflowDiagram` — DAG layout with topological-depth row assignment, status badges (pending/running/completed/error/skipped), straight-line edge routing.
- **Golden-snapshot presets** extended from 1 to 4: `python_minimal`, `node_minimal`, `rust_minimal`, `multi_backend`. Regenerate with `UPDATE_GOLDEN=1`.
- **OpenAPI contract tests** (`tests/test_openapi_contract.py`): every entity's emitted OpenAPI has typed properties, correct required-vs-optional split, format declarations for uuid/date-time, `$ref` for enums. 13 tests.
- **Mutation testing configuration** — `[tool.mutmut]` in pyproject.toml scoped to `feature_injector`, `merge`, `provenance`, `injectors/*`, `updater`. New `docs/operations/mutation-testing.md` documents the kill-rate baseline + the "run locally on breaking-change PRs" policy.
- **Vue / Svelte dependencies** — `highlight.js@^11.10.0` for CodeViewer.
- **Dart dependencies** — `flutter_highlight@^0.7.0` for CodeViewer, `http@^1.2.0` for MCP client.

### Changed

- Published packages bumped to `1.0.0-alpha.5`. `@forge/canvas-vue` + `@forge/canvas-svelte` + `forge_canvas` now export all 5 canvas components.

### Tests

662 passing, 1 skipped (up from 646).

---

## [1.0.0a4] - 2026-04-20

> Fourth alpha. Closes the plugin SDK story (command dispatcher wired, frontends pluggable, fragments shippable from plugin packages), adds runtime canvas-prop lint across Vue / Svelte / Flutter, ships the MCP approval-token + audit-log middleware, and lifts the first canvas component (`Report`) into the published packages as the extraction reference.

### Breaking

- **`/mcp/invoke` now requires a valid `approval_token` when the tool's mode is not `auto`**. Frontend ApprovalDialog code must first POST `/mcp/approval/mint` and include the returned token on the next invoke. Tokens are HMAC-signed with `MCP_APPROVAL_SIGNING_KEY` and bound to (server, tool, input-hash). Generated projects without the env var fall back to the service name — logged as a warning; not safe for production.

### Added

- **Plugin command dispatcher** (A4-2): `ForgeAPI.add_command(name, handler)` now registers into `COMMAND_REGISTRY`; `_build_parser` walks it and injects `--<name>` flags; main.py dispatches to the handler with the full argparse Namespace. Collisions with core flags are silently skipped so a malformed plugin can't break the CLI.
- **Plugin fragment path resolver** (A4-3): `FragmentImplSpec.fragment_dir` now accepts absolute paths — plugins can ship fragments from their own package tree (`Path(__file__).parent / "fragments" / ...`) instead of being restricted to forge's `_fragments/` directory.
- **Plugin-defined frontends** (A4-4): `ForgeAPI.add_frontend(value, FrontendSpec)` + `_PluginFramework` sentinel + `resolve_frontend_framework`, mirroring the 1.0.0a2 backend-plugin story. Plugins can ship Solid, Qwik, Remix, etc.
- **Canvas runtime lint** (A4-5): `lintProps` / `warnOnLintIssues` in every canvas package. `CanvasRegistry.lintAndResolve(name, props)` validates props against the component's JSON Schema in dev mode and emits `console.warn` / `debugPrint` on drift. Tree-shaken in prod.
- **MCP audit log + approval tokens** (A4-6): `app/mcp/audit.py` in generated projects — HMAC-SHA256-signed approval tokens bound to (server, tool, input-hash), append-only `audit.jsonl` recording every invocation with user_id + decision + error. New `POST /mcp/approval/mint` endpoint for frontend token exchange.
- **Canvas Report component extracted** (A4-7): `Report.vue`, `Report.svelte`, `report.dart` now live in the published `@forge/canvas-vue`, `@forge/canvas-svelte`, and `forge_canvas` packages. The Vue + Svelte versions ship with `marked` + `DOMPurify` as runtime deps; Flutter uses `flutter_markdown`. Packages bumped to `1.0.0-alpha.4`.

### Changed

- `api.py` adds `add_frontend`; `config.py` adds `FrontendSpec`, `PLUGIN_FRAMEWORKS`, `FRONTEND_SPECS`, `_PluginFramework`, `register_frontend_framework`, `resolve_frontend_framework`.
- `feature_injector._resolve_fragment_dir` extracts the path-resolution policy so plan.py and the injector share it.
- `plugins.py` adds `COMMAND_REGISTRY`; `reset_for_tests` clears it.
- MCP router: `/mcp/invoke` now verifies approval tokens, writes audit entries, and returns 401 on token failure.

### Tests

646 passing, 1 skipped (up from 618). New suites: `test_plugin_commands.py` (6), `test_plugin_fragment_paths.py` (4), `test_plugin_frontend.py` (8), `test_mcp_audit.py` (10).

---

## [1.0.0a3] - 2026-04-20

> Third alpha. Three-way merge runtime (`merge` zone is now semantically real), real MCP subprocess spawning + tool discovery, Svelte and Flutter MCP UI parity, and the TypeSpec compiler bridge for domain entities.

### Breaking

- **`merge` zone now emits `.forge-merge` sidecars on conflict.** Previously aliased to `generated`. If you had `zone: merge` in a fragment's inject.yaml, a user edit + fragment change during `forge --update` now produces a sidecar instead of silently overwriting. Pre-1.0.0a3 projects that don't have `[forge.merge_blocks]` in their `forge.toml` fall back to `generated` behavior on first apply, then record a baseline going forward.
- **MCP endpoints return real data.** `/mcp/tools` spawns configured servers and aggregates their advertised tools; `/mcp/invoke` proxies real tool calls. The 1.0.0a1 placeholder response is gone. Projects without `mcp.config.json` see an empty tool list.

### Added

- `forge/merge.py` — pure three-way-decision helpers (`three_way_decide`, `write_sidecar`, `sha256_of_text`, `MergeBlockCollector`). No I/O in the decision function; callers handle disk writes.
- `[forge.merge_blocks]` table in `forge.toml` — per-block SHA baseline keyed by `{rel_path}::{feature_key}:{marker}`. Enables true three-way compare between baseline / current / new.
- Python MCP stdio JSON-RPC client (`app/mcp/client.py` in generated projects): `McpStdioClient` speaks newline-delimited JSON-RPC 2.0, completes the initialize handshake, and handles `tools/list` + `tools/call`. `McpRegistry` manages per-server subprocess lifecycle; app shutdown hook terminates children cleanly.
- **Svelte MCP UI** (`mcp_ui_svelte` fragment): `ToolRegistry.svelte` with runes-based fetch, `ApprovalDialog.svelte` with `$props()` rune, `use-mcp-tools.svelte.ts` client-side hook with session-approval cache.
- **Flutter MCP UI** (`mcp_ui_flutter` fragment): `tool_registry.dart` `FutureBuilder`-backed list, `approval_dialog.dart` Material dialog returning `ApprovalResult`, `mcp_client.dart` ties them together with session-approval cache.
- `forge/domain/typespec.py` — TypeSpec compiler bridge via `npx tsp compile`. `typespec_available()` for toolchain detection; `compile_tsp()` + `extract_entities()` produce YAML-DSL-compatible entity dicts from `.tsp` sources so the existing emitters (Pydantic / Zod / sqlx / OpenAPI) consume either format.

### Changed

- `_apply_zoned_injection` threads `project_root` + `collector` through so `merge` zone can look up baselines and record new ones.
- `generator._write_forge_toml` and `updater._restamp_forge_toml` now emit `[forge.merge_blocks]` alongside `[forge.provenance]`.
- MCP fragment (`mcp_server/python`) gains `client.py`, a shutdown hook injection, and real `/mcp/tools` + `/mcp/invoke` implementations.
- Fragment registry grows from 47 → 51 entries (`mcp_ui_svelte`, `mcp_ui_flutter`).

### Tests

634 passing, 1 skipped (up from 594). New suites: `test_three_way_merge.py` (12), `test_mcp_client.py` (4), `test_typespec_bridge.py` (8).

---

## [1.0.0a2] - 2026-04-20

> Second alpha. Completes the ports-and-adapters refactor (6 RAG adapters, 4 LLM providers, queue + object-store ports) and adds plugin-extensible `BackendLanguage`, the ts-morph AST sidecar, Node base-template anchors for reliability auto-wire, and retires the hand-rolled Flutter SSE client in favor of the `forge_canvas` package.

### Breaking

- **`rag.backend` now enables the port+adapter pair** instead of the legacy `rag_<name>` fragments. Generated projects get `vector_store_port` + `vector_store_<provider>` in their plan. Runtime-swappable via env. Migration for pre-1.0.0a2 projects: `forge --migrate --migrate-only adapters`.

### Added

- **Full RAG port+adapter catalogue:** `vector_store_chroma`, `vector_store_pinecone`, `vector_store_milvus`, `vector_store_weaviate`, `vector_store_postgres` join the 1.0.0a1 Qdrant reference.
- **LLM provider port** (`llm_port`) + four adapters: `llm_openai`, `llm_anthropic`, `llm_ollama`, `llm_bedrock`. New `llm.provider` option.
- **Queue port** (`queue_port`) with Redis-list + AWS SQS adapters. New `queue.backend` option.
- **Object-store port** (`object_store_port`) with S3 (+ S3-compatible) and local-filesystem adapters. New `object_store.backend` option.
- **Plugin-extensible `BackendLanguage`** — plugins can add new backend languages (e.g. Go, Java) via `api.add_backend("go", spec)`. Built on a `_PluginLanguage` sentinel + `resolve_backend_language(value)` helper.
- **ts-morph subprocess sidecar** (`forge/injectors/ts-morph-helper.mjs` + `ts_morph_sidecar.py`). Opt-in via `FORGE_TS_AST=1`; falls back to the regex injector when ts-morph or Node isn't available.
- **Node base-template markers** — `FORGE:PRISMA_CLIENT_INIT` anchors the reliability_connection_pool auto-wire so `reliability.connection_pool=true` produces a working generated project without hand-edits.
- **Flutter hand-rolled SSE deprecation notice** + migration target (`forge_canvas` package). `forge --migrate --migrate-only ui-protocol` renames the legacy file to `.legacy`.

### Changed

- `rag.backend` enum's `enables` map now points at vector_store_* fragments; legacy `rag_<provider>` fragments remain in the registry for backward-compat but aren't selected by the Option.
- Fragment registry grows from 35 → 47 entries (ports + adapters + LLM/queue/object-store).
- Option registry grows from 27 → 30 (`llm.provider`, `queue.backend`, `object_store.backend`).

### Tests

594 passing, 1 skipped (up from 571). New test files: `test_plugin_backend_language.py`, `test_ts_morph_sidecar.py`.

---

## [1.0.0a1] - 2026-04-20

> First alpha of the **1.0 clean-break** series. See `RELEASING.md` for the release process and `UPGRADING.md` for migration guidance.

### Breaking

- **CLI entry point moved** — `forge.cli:main` → `forge.cli.main:main`. The `forge` console script is unchanged; only direct Python imports of private helpers need to update.
- **`forge.toml` gains `[forge.provenance]`** — per-file origin + SHA-256 + fragment version. Old projects receive a one-time backfill on first `forge --update` in 1.0.

### Added

**Phase 0 — foundations:**
- `forge/cli/` package (decomposed from the 1,361-line cli.py) with command-object dispatch.
- `forge/provenance.py` — CRLF-normalized SHA-256 + classify/record primitives; written to `[forge.provenance]` on every generate, consumed by `forge --update`.
- `forge/api.py` + `forge/plugins.py` — entry-point plugin host (group `forge.plugins`), `ForgeAPI` facade, `forge plugins list` command.
- `forge --plan` (ordered fragment plan + mutation tree, ASCII-safe on Windows) and `forge --dry-run`.

**Phase 1 — schema-first core:**
- `forge/codegen/ui_protocol.py` emits TS + Dart + Pydantic from 7 JSON schemas under `forge/templates/_shared/ui-protocol/`.
- `forge/codegen/canvas_contract.py` + 5 canvas component props schemas; `forge --canvas lint` validates payloads.
- `forge/domain/` — YAML entity DSL with Pydantic / Zod / sqlx / OpenAPI emitters (TypeSpec adoption: 1.0.0a2).
- `forge/codegen/enums.py` — Python / TS / Zod / Rust / Dart emitters for shared enums.
- `forge/codegen/pipeline.py` — integration point: runs every emitter during `forge new` so each project ships with regenerated types per-frontend and per-backend.

**Phase 2 — extensibility:**
- `forge/injectors/python_ast.py` — LibCST-anchored Python injection that survives Ruff / Black reformatting; falls back to text markers on syntax errors.
- `forge/injectors/ts_ast.py` — regex-anchor + sentinel injector for `.ts/.tsx/.js/.jsx/.mjs`; dispatched automatically by extension.
- Three-zone merge — `generated`, `user`, and `merge` semantics on every `inject.yaml` entry.
- Reference port+adapter pair: `vector_store_port` + `vector_store_qdrant` (rest of RAG refactor: 1.0.0a2).
- `docs/architecture-decisions/ADR-001-pragmatic-hexagonal.md`, `ADR-002-ports-and-adapters.md`.
- `docs/guides/plugins.md` + `examples/forge-plugin-example/` reference plugin.

**Phase 3 — agentic UI:**
- Published package scaffolds: `@forge/canvas-vue`, `@forge/canvas-svelte`, `forge_canvas` (pub.dev), each with Vite library build / tsconfig / svelte.config / analysis_options so they can actually `npm publish` / `flutter pub publish`.
- Dart `AgUiClient` — exponential-backoff reconnect + `Last-Event-ID` resume + SSE chunk parsing.
- `ForgeTheme` — shadcn-flavored Material 3 matching the web design language.
- MCP scaffolds: `mcp_server` (FastAPI router for `/mcp/tools` + `/mcp/invoke`) + `mcp_ui` (Vue ToolRegistry + ApprovalDialog).
- `docs/reference/mcp.md` + `mcp.config.example.json` + JSON Schema at `forge/templates/_shared/mcp/mcp_config_schema.json`.

**Phase 4 — production polish:**
- Reliability fragments across **Python / Node / Rust**: `reliability_connection_pool`, `reliability_circuit_breaker`, with auto-wire injections.
- `observability_otel` fragment across **Python / Node / Rust** (OTLP exporter + FastAPI / `@opentelemetry/sdk-node` / `tracing-opentelemetry` bridges).
- Security fragments: `security_csp` (strict CSP nginx include), `security_sbom` (CycloneDX workflow).
- `forge/common_files.py` drops `.editorconfig`, `.gitignore`, `.pre-commit-config.yaml`, and per-backend CI workflows into every project.
- `forge/doctor.py` — toolchain / Docker / port / `forge.toml` integrity diagnostics via `forge --doctor [--json]`.
- New CLI verbs: `forge --new-entity-name`, `--add-backend-language`, `--preview`, `--migrate [--migrate-only] [--migrate-skip]`.
- `forge/migrations/` — three codemods (`migrate-ui-protocol`, `migrate-entities`, `migrate-adapters`) + umbrella runner.
- Golden snapshot test suite (`UPDATE_GOLDEN=1` regenerates).
- `.github/workflows/release.yml` — coordinated PyPI + npm + pub.dev publish on tag push (TestPyPI for pre-releases, PyPI for stable).

### Changed

- `forge/cli.py` (1,361 lines) decomposed into `forge/cli/` package.
- `capability_resolver.resolve()` returns a richer plan consumed by `forge --plan` and `forge --dry-run`.
- `updater.update_project()` classifies each tracked file as unchanged / user-modified / missing using the provenance manifest.
- Fragment registry grows from 27 → 35 entries (vector-store ports, reliability / observability / security, MCP).
- Option registry grows from 22 → 27 with `reliability.connection_pool`, `reliability.circuit_breaker`, `observability.otel`, `security.csp`, `security.sbom`, `platform.mcp`.

### Tests

571 passing, 1 skipped (up from 367). 13 new test files covering provenance, plugins, --plan/--dry-run, codegen pipeline, canvas contract, domain DSL, enum codegen, UI-protocol codegen, Python + TS AST injection, three-zone merge, doctor, migrations, new CLI verbs, common files, golden snapshots, and end-to-end generation with reliability + observability options enabled.

---

## [Unreleased — 0.x maintenance]

### Added — Svelte + Flutter frontend parity

- **Copier configuration parity**: Svelte gains `include_openapi`, `default_color_scheme`, `app_title`, `api_proxy_target`, and hidden multi-backend vars (`backend_features`, `proxy_targets`, `vite_proxy_config`). Flutter gains `default_color_scheme`, `app_title`, and `backend_features`. `forge.variable_mapper.svelte_context` / `flutter_context` extended to emit them.
- **Multi-backend awareness in generated frontends**: Svelte `_build/post_generate.py` patches per-feature `api/{name}.ts` to call `/api/{backend}/v1/...` and injects `vite_proxy_config` into `vite.config.ts`. Flutter `_tasks/post_generate.py` patches generated `*_repository.dart` HTTP paths and writes `lib/src/core/config/backend_routes.dart`.
- **Svelte AG-UI chat core** (`apps/svelte-frontend-template/template/src/lib/features/chat/`): full port of the Vue `ai_chat` module — `agent-client.svelte.ts` wrapping `@ag-ui/client`, streaming text deltas, tool-call lifecycle, HITL prompts, JSON-Patch state reducer, model selector, approval mode toggle, soft-imported auth so the chat compiles in `include_auth=false` projects too. New deps: `@ag-ui/client`, `@ag-ui/core`, `@modelcontextprotocol/ext-apps`, `fast-json-patch`, `marked`, `dompurify`.
- **Flutter Dart-native AG-UI chat core** (`apps/flutter-frontend-template/{{project_slug}}/lib/src/features/chat/`): pure Dart implementation of the AG-UI protocol — `AgUiClient` consuming Dio SSE streams, sealed `AgUiEvent` union, pure `reduce()` function applying RFC 6902 JSON Patch via `json_patch`, Riverpod `ChatNotifier` + selectors, widgets for chat panel / message bubble / tool-call chip / user-prompt card / agent status bar. New deps: `flutter_markdown`, `markdown`, `json_patch`, `shimmer`. Reducer covered by a 12-case unit test.
- **Workspace pane parity** (both frameworks): `WorkspacePane` + registry pattern, `FileExplorer`, `CredentialForm`, `ApprovalReview`, `UserPromptReview`, `FallbackActivity`, plus `AgUiEngine` and `McpExtEngine`. The Flutter MCP engine renders activities natively (no iframe sandbox — explicit deferral).
- **Canvas pane parity** (both frameworks): `CanvasPane` + registry, `DynamicForm`, `DataTable`, `Report` (markdown), `CodeViewer`, `WorkflowDiagram` (minimum-viable diagram), `Fallback`.

### Changed

- Svelte chat state model rewritten from a 1.5s setTimeout simulation to the AG-UI agent client (`chat.svelte.ts` exposes the same `getChatStore()` surface, but messages now stream from a real agent endpoint).
- Flutter `ChatMessage` freezed model lost the `timestamp` field (AG-UI messages don't carry one) and gained `isStreaming` semantics; existing chat_message_test updated.
- Generated READMEs gain a "Chat & agentic UI" section with usage and registry-extension examples.

### Added — Static analysis & CI hardening (prior)

- `ruff` + `ty` strict-ish config, `.pre-commit-config.yaml`, `.github/workflows/ci.yml` matrix (Linux + Windows × Python 3.11/3.12/3.13). Coverage floor raised to 75%.
- **`GeneratorError`** propagates clean error messages through `--json` (single-line envelope, exit 2) and stderr (`Generation failed: ...`, exit 2). `_run_backend_cmd(..., required=True)` raises on failure; `_git_init` checks every step.
- **End-to-end harness** (`tests/e2e/test_full_generation.py`): scaffolds python+vue, node+svelte, rust+none, and the multi-backend python+node+rust+vue+keycloak case, then runs the generated scaffold's native test suite. Marked `@pytest.mark.e2e`; nightly workflow at `.github/workflows/e2e.yml`.
- **`BACKEND_REGISTRY`** in `forge/config.py` drives language dispatch (CLI prompts, generator, variable mapper). Adding a 4th backend is now a one-day task — see [docs/adding-a-backend.md](docs/guides/adding-a-backend.md).
- **`forge.toml`** stamped into every generated project (forge version + per-language template paths) so projects can be re-rendered with `copier update`.
- **Keycloak realm validation**: `render_keycloak_realm` parses the rendered JSON and asserts essential top-level keys before writing. Jinja typos fail generation immediately rather than at Keycloak boot.
- **`--verbose`** flag overrides `--quiet` for full Copier + subprocess output (works in JSON mode too — diagnostic output goes to stderr).
- **`forge --completion bash|zsh|fish`** prints a shell completion script.
- **Documentation**: `docs/architecture/generator.md` (Mermaid diagram), `docs/guides/adding-a-backend.md`, `CONTRIBUTING.md`, this changelog.

### Changed

- `ProjectConfig.validate` split into `_validate_backend_uniqueness`, `_validate_features_against_reserved`, `_validate_ports`, `_validate_keycloak_ports`. Behavior preserved.
- Interactive prompts unified — every backend now prompts for its language version (was previously skipped on the second-and-later backends).
- `backend_context` / `node_backend_context` / `rust_backend_context` collapsed to one function driven by `BACKEND_REGISTRY`. The legacy names remain as aliases.
- `_build_config` (105 lines) split into `_build_backends_from_cfg`, `_build_frontend_from_cfg`, and a slim orchestrator.

### Fixed

- `_git_init` previously ran three `subprocess.run` calls without `check=True`; a failed commit produced a "successful" generation with no commit. Each step is now checked.
- `_generate_frontend` / `render_frontend_dockerfile` no longer assume `config.frontend` is non-None — both now raise `GeneratorError` if called without a frontend.
- `BackendConfig` import was missing at module level in `forge/generator.py`; types now resolve under `ty check`.
- Stale tests in `tests/test_generator.py` (expected `<root>/test_app-e2e`) and `tests/test_e2e_templates.py` (expected old test descriptors) updated to match current template output.

## [0.1.0] - Initial release

- CLI scaffolds full-stack projects via Copier + uv.
- Backends: Python (FastAPI), Node.js (Fastify), Rust (Axum). Multi-backend per project.
- Frontends: Vue 3, Svelte 5, Flutter web.
- Optional Keycloak + Gatekeeper auth with multi-tenant isolation.
- Headless mode via `--config`, `--yes`, `--json` for CI and AI-agent integration.
- Auto-generated Playwright e2e suite per project.
