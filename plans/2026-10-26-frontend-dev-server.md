# Forge: a dev-server overlay for the Compose frontend

Base: 53fd321a (`kartrainc/forge` main, PR #8).

Date: 2026-10-26. Status: implemented on `feat/frontend-dev-server` (Vue); in review. Decision record:
[ADR-013](../docs/architecture-decisions/ADR-013-a-dev-server-overlay-for-the-compose-frontend.md).

Requested scope: let the generated Vue frontend run as a Vite dev server behind
the same Traefik router in the Compose stack, so an edit under `src/` shows in
the browser without an image build or a page reload. Opt-in; Svelte follows
with the same tests; the off state's Dockerfile and Compose output do not
change.

## Decisions

| Area | Choice |
| --- | --- |
| Option | `FrontendConfig.dev_server`, boolean, default `false`, Vue first, Flutter refused; carried like `ui_kit` |
| Dockerfile | with the field on: `deps → source → dev` and `source → builder → nginx`; off: unchanged |
| Runtime user | `dev` runs non-root; the tree and Vite's cache directories are writable by it; `COPY --chown` |
| Overlay | generated `docker-compose.dev.yaml`; `volumes: !reset []` and `healthcheck: !override`, and only the service-port label (labels merge by key, so a downstream overlay's hosts survive); Compose ≥ 2.39.4 (`initial_sync`) |
| Sync | Compose Watch: one `sync` rule over the app directory with `initial_sync: true`, and separate `rebuild` rules for `package.json` and the lockfile; Vite restarts itself on config changes |
| Vite | generated wrapper `vite.dev.config.ts`; object, function and promise exports; `allowedHosts` set to `app.localhost` plus `FORGE_DEV_ALLOWED_HOSTS` after the project's config (Vite still admits localhost, `*.localhost` and IPs); `strictPort`; no polling unless measured |
| Auth | dev behaves like the built stack: the gatekeeper's browser paths are not routed; `VITE_AUTH_DISABLED` is not overridden; the Vite port is not published, so the origin, cookie and realm redirect URIs are unchanged |
| Several frontends | ADR-012 is on `main`: one overlay with a service per app that has the option, each with its own port and host |
| Boot | with the field on, forge skips its automatic `docker compose up` and prints the Watch command |
| Fallback | documented bind mount for Watch performance or filesystem problems on Compose ≥ 2.39.4; best effort, existing paths only |

## Grounding — forge at the base

Read at the base on 2026-10-26.

| Area | Fact |
| --- | --- |
| Dockerfile | `forge/templates/deploy/Dockerfile.node.j2`: one `builder` stage (package-manager setup, manifest copy and install, `COPY . .`, build), then `FROM nginx:alpine`. No `USER`. Rendered by `render_frontend_dockerfile`; the same template serves Vue and Svelte (`build_dir` differs). |
| Compose | `docker-compose.yml.j2`: the one `frontend` service has a build from `./apps/{{ frontend_slug }}`, one nginx.conf volume, four Traefik labels (rule `Host(app.localhost)`, entrypoint `web`, service port 80), `depends_on` backends, the network, and **no health check**. |
| Vite config | the Vue template's `vite.config.ts.jinja` sets `server.port` to the frontend's `server_port` (5173 by default) and a dev proxy; it writes `src/auto-imports.d.ts` (and `src/components.d.ts` under Nuxt UI) at startup. The template has no `public/`; `.env.development` exists. Svelte's `dev` script is `vite dev`; its static directory is `static/`. |
| Build context | `.dockerignore` (`dockerignore.node.j2`) keeps `node_modules`, build output and `.env.*.local` out of the context. |
| Ownership | `ownership()` in `forge/quality/model.py`: non-source files (YAML, Dockerfile) and base-template `.ts` outside the generated paths are `scaffold`; an edited scaffold gets a `.forge-merge` sidecar on update, an untouched one is rewritten, a vanished one is left on disk. |
| Auth routes | the generated gatekeeper defines only the ForwardAuth middleware and no router; no browser path is routed on the frontend host. |
| Compose floor | `tests/e2e/test_platform_compose_boot.py` already requires `!reset` (2.24+); `!override` needs 2.24.4 and `develop.watch.initial_sync` 2.39.4 (docker/compose#13232), which sets the floor. |
| Browser tooling | `forge/quality/dependencies.py` installs Chromium for Vue and Svelte; CI has Docker-capable jobs. |

## Changes

One pull request for the Vue implementation, on one branch. Its first commit is
this plan, ADR-013 and the index row, based on `main`; the number 013 leaves
012 to the list-of-frontends ADR. The commits after it
carry the tests of the behaviour they add. Svelte is a second pull request, and
adopting it in an existing project is a change in that project.

### Vue implementation

1. **Config.** `FrontendConfig.dev_server`; validation (Vue only, refuse
   Flutter and Svelte until the Svelte change); stack key, `--dev-server`, interactive
   prompt; `[forge.frontend]` read and write; recipe round trip.
2. **Dockerfile.** The `dev` stage per ADR-013 decision 2, in
   `Dockerfile.node.j2`, for npm, pnpm, yarn and bun. Built: `deps` installs as
   root, then makes only `/app`, `node_modules` and Vite's two cache directories
   the `node` user's (a non-recursive `chown`, so no `node_modules` layer is
   duplicated); `source` copies with `--chown=node:node`; pnpm's corepack cache
   lives outside `/root`. Proven by building and running it (see Tests).
3. **Overlay and wrapper.** `docker-compose.dev.yaml` and `vite.dev.config.ts`
   from `docker_manager.py`, from the same context as the base Compose file;
   the per-package-manager dev command;
   the Node health check; the watch rules, with only paths that exist.
   `tsconfig.node.json` includes the wrapper.
4. **Doctor and boot.** `forge/doctor.py` reports a Docker Compose older than
   2.39.4 when a project enables the field, with tests in the doctor's suite;
   `boot()` and its call in `forge/cli/main.py` skip the automatic start for a
   project with the field on and print the Watch command, tested for `--yes` and
   for the interactive path.
5. **Update and removal.** Provenance for the new files; `false → true` adds
   them and the `dev` stage (a sidecar if the Dockerfile was edited);
   `true → false` removes the overlay and wrapper when unmodified and reports an
   edited one; a modified overlay receives a sidecar.
6. **Docs.** The generated README and docs: the command, the minimum Compose,
   what dev does not cover (CSP, auth, nginx), the bind-mount fallback and its
   caveats. `UPGRADING.md`. The changelog is updated on release, not per pull request.

### Svelte parity (second pull request)

The same, for the Svelte template: `static/` rather than `public/`,
its own dev command and Vite wrapper facts, which that change verifies first, and
the same tests.

### Adopting it in an existing project (separate)

Move the generator pin; merge scaffold sidecars; append the overlay last in
`COMPOSE_FILE` when the stack is assembled through an env file; decide which
shared build contexts are watched and which need a rebuild; widen
`server.fs.allow` in its `vite.config.ts` for aliases that resolve outside the
frontend root; keep its own auth routes and gatekeeper overlay as its own concern.

## Tests

| Test | Proves |
| --- | --- |
| render, off | Dockerfile, Compose and `.dockerignore` byte-identical to the base for every package manager; the recipe gains `dev_server = false`; `forge.toml` and every other file are unchanged |
| render, on | stages `deps → source → dev`, `source → builder → nginx`; nginx last; one `COPY . .`; `dev` non-root with `COPY --chown` |
| merged Compose | `docker compose config` with both files: the dev service has no nginx volume, only the service-port label is set and a downstream overlay's extra host survives, a Node health check, `develop.watch` with `initial_sync` and the expected rules |
| wrapper | object, function and promise exports resolve; the guarded keys are set after the project's config, not merged; at run time a project's `allowedHosts: true` still refuses `evil.example` (external names; `*.localhost` is Vite's own allowance) |
| updates | false → true, true → false (no stale files), edited overlay and edited Dockerfile give sidecars |
| options | `--dev-server`, the stack key and the interactive prompt (Vue only) reach the config; asking for it without a Vue frontend is an error; Svelte is not asked |
| doctor and boot | `forge doctor` flags Compose below 2.39.4 when the field is on; `--yes`, the interactive confirm and a frontend-only project do not run `docker compose up`, and print the Watch command |
| readme | the generated app README gains the Compose section only with the field on |
| ownership | the dev server writes nothing into the checkout as a different uid; Vite's cache directory is writable |
| runtime HMR | Playwright through Traefik, started with `up --watch --build`: edit a watched source component, the DOM changes, and the document did not navigate or reload (a `window` marker survives, no new main-frame navigation); a `package.json` change rebuilds |
| matrix | render tests for npm, pnpm and yarn (stages, `CMD`, corepack location) and for shadcn and nuxt-ui. Runtime coverage is npm, pnpm and yarn with shadcn and sign-in off; nuxt-ui and sign-in on are not run |
| origin | the overlay publishes no port for the frontend and sets no `environment`, so the rendered `.env` value of `VITE_AUTH_DISABLED` reaches the dev server unchanged |
| production | the existing Compose, Helm and CSP tests still pass unchanged with the field off |

## Risks and open points

- **Watch latency and file events on Docker Desktop.** Sync writes into the
  container, so the watcher inside it should see events; the Playwright test
  showed polling is not needed on Linux. Measure on Linux and Docker Desktop
  (Windows or macOS) before the pull request merges, or the docs say Linux only.
- **Single-file watch paths.** Compose Watch rules on individual files
  (`vite.config.ts`, the lockfile) need checking against the Compose versions at
  the floor.
- **Deleting on `true → false`.** The updater's general rule leaves vanished
  scaffold on disk. The exception is limited to the overlay and wrapper and to
  unmodified files; it is a new rule, so it is called out for review.
- **pnpm and yarn.** Run end to end now. pnpm needed a fix shared with the production build (the image installed `pnpm@latest`, whose `minimumReleaseAge` policy refused a lockfile the host's pnpm 10 had just written; the Dockerfile now installs pnpm 10). Yarn failed only on a host Node below the project's engine range (jsdom wants 22.22.2, 24.15 or 26).
- **Nuxt UI and sign-in on at run time.** Not run; they rest on the render tests and the Nuxt UI quality lane.
- **Port.** The overlay reads the frontend's `server_port` rather than assuming
  5173.
- **Svelte unknowns.** Its dev command, wrapper and generated files are not
  verified here; the Svelte change starts by reading them.
