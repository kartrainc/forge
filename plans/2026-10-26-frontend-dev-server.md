# Forge: a dev-server overlay for the Compose frontend

Base: c2e41854 (`kartrainc/forge` main, PR #4).

Date: 2026-10-26. Status: proposed; no code written. Decision record:
[ADR-013](../docs/architecture-decisions/ADR-013-a-dev-server-overlay-for-the-compose-frontend.md).
First consumer: kartra-ai, which plans its own adoption in its repository.

Requested scope: let the generated Vue frontend run as a Vite dev server behind
the same Traefik router in the Compose stack, so an edit under `src/` shows in
the browser without an image build or a page reload. Opt-in; Svelte follows
with the same tests; the off state's Dockerfile and Compose output do not
change. Tracked as KR-59.

## Decisions

| Area | Choice |
| --- | --- |
| Option | `FrontendConfig.dev_server`, boolean, default `false`, Vue first, Flutter refused; carried like `ui_kit` |
| Dockerfile | with the field on: `deps → source → dev` and `source → builder → nginx`; off: unchanged |
| Runtime user | `dev` runs non-root; the tree and Vite's cache directories are writable by it; `COPY --chown` |
| Overlay | generated `docker-compose.dev.yaml`; `!override` for volumes, labels, healthcheck; Compose ≥ 2.24.4 |
| Sync | Compose Watch, `initial_sync: true`; sync, sync+restart for Vite configs, separate rebuild rules for `package.json` and the lockfile |
| Vite | generated wrapper `vite.dev.config.ts`; object, function and promise exports; exact `allowedHosts` (`app.localhost`); `strictPort`; no polling unless measured |
| Auth | dev behaves like the built stack: the gatekeeper's browser paths are not routed; `VITE_AUTH_DISABLED` is not overridden; the Vite port is not published, so the origin, cookie and realm redirect URIs are unchanged |
| Multi-frontend | not assumed; if kartrainc/forge#6 lands first, the overlay gains one override per enabled frontend |
| Boot | with the field on, forge skips its automatic `docker compose up` and prints the Watch command |
| Fallback | documented bind mount for Watch performance or filesystem problems on Compose ≥ 2.24.4; best effort, existing paths only |

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
| Compose floor | `tests/e2e/test_platform_compose_boot.py` already requires `!reset` (2.24+). |
| Browser tooling | `forge/quality/dependencies.py` installs Chromium for Vue and Svelte; CI has Docker-capable jobs. |

## Changes

One pull request for the Vue implementation, on one branch. Its first commit is
this plan, ADR-013 and the index row, based on `main`; the number 013 leaves
012 to the list-of-frontends ADR in kartrainc/forge#6. The commits after it
carry the tests of the behaviour they add. Svelte is a second pull request, and
the Kartra adoption is a change in kartra-ai.

### Vue implementation

1. **Config.** `FrontendConfig.dev_server`; validation (Vue only, refuse
   Flutter and Svelte until the Svelte change); stack key, `--dev-server`, interactive
   prompt; `[forge.frontend]` read and write; recipe round trip.
2. **Dockerfile.** The `dev` stage per ADR-013 decision 2, in
   `Dockerfile.node.j2`, for npm, pnpm, yarn and bun. Settle the ownership
   mechanism by measurement: `deps` run as a non-root user from the start
   (with the package manager's cache outside `/root`), against a `chown` in
   `dev`. Choose the one with the smaller image and no root-owned cache; record
   the numbers in the pull request.
3. **Overlay and wrapper.** `docker-compose.dev.yaml` and `vite.dev.config.ts`
   from `docker_manager.py`, from the same context as the base Compose file;
   the per-package-manager dev command;
   the Node health check; the watch rules, with only paths that exist.
   `tsconfig.node.json` includes the wrapper.
4. **Doctor and boot.** `forge/doctor.py` reports a Docker Compose older than
   2.24.4 when a project enables the field, with tests in the doctor's suite;
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

### Kartra adoption (in kartra-ai, separate)

Move the generator pin; merge scaffold sidecars; append the overlay last in
`COMPOSE_FILE` through an env file; decide whether `libs/kartra-pricing` and
the geographic data are watched or need a rebuild; widen `server.fs.allow` in its
`vite.config.ts` for the aliases that resolve outside the frontend root; keep its own auth routes and
gatekeeper overlay as its own concern.

## Tests

| Test | Proves |
| --- | --- |
| render, off | Dockerfile, Compose and `.dockerignore` byte-identical to the base for every package manager; recipe and manifest gain only `dev_server = false` |
| render, on | stages `deps → source → dev`, `source → builder → nginx`; nginx last; one `COPY . .`; `dev` non-root with `COPY --chown` |
| merged Compose | `docker compose config` with both files: the dev service has no nginx volume, the labels equal the base's except the service port, a Node health check, `develop.watch` with `initial_sync` and the expected rules |
| wrapper | object, function and promise base configs; exact `allowedHosts`; `strictPort`; host `0.0.0.0` |
| updates | false → true, true → false (no stale files), edited overlay and edited Dockerfile give sidecars |
| doctor and boot | `forge doctor` flags Compose below 2.24.4 when the field is on; `forge --dev-server --yes` and the interactive confirm do not run `docker compose up`, and print the Watch command |
| ownership | the dev server writes nothing into the checkout as a different uid; Vite's cache directory is writable |
| runtime HMR | Playwright through Traefik, started with `up --watch --build`: edit a watched source component, the DOM changes, and the document did not navigate or reload (a `window` marker survives, no new main-frame navigation); a `package.json` change rebuilds |
| matrix | npm, pnpm, yarn; shadcn and nuxt-ui; `include_auth` on and off |
| origin | the overlay publishes no port for the frontend, and the rendered `.env` value of `VITE_AUTH_DISABLED` reaches the dev server unchanged |
| production | the existing Compose, Helm and CSP tests still pass unchanged with the field off |

## Risks and open points

- **Watch latency and file events on Docker Desktop.** Sync writes into the
  container, so the watcher inside it should see events; the Playwright test
  decides whether polling is needed. Measure on Linux and Docker Desktop
  (Windows or macOS) before the pull request merges, or the docs say Linux only.
- **Single-file watch paths.** Compose Watch rules on individual files
  (`vite.config.ts`, the lockfile) need checking against the Compose versions at
  the floor.
- **Deleting on `true → false`.** The updater's general rule leaves vanished
  scaffold on disk. The exception is limited to the overlay and wrapper and to
  unmodified files; it is a new rule, so it is called out for review.
- **Docker image size.** A non-root `deps` stage may double `node_modules` in a
  layer; the measurement in step 2.2 decides.
- **Port.** The overlay reads the frontend's `server_port` rather than assuming
  5173.
- **Svelte unknowns.** Its dev command, wrapper and generated files are not
  verified here; the Svelte change starts by reading them.
