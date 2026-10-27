# ADR-013: A dev-server overlay for the Compose frontend

- Status: Proposed
- Author: forge team
- Date: 2026-10-26
- Scope: `FrontendConfig.dev_server` (`forge/config/_frontend.py`), the stack
  file's `frontend.dev_server` and `--dev-server` (`forge/cli/builder.py`,
  `forge/cli/interactive.py`), `forge/templates/deploy/Dockerfile.node.j2`,
  a generated `docker-compose.dev.yaml`, a generated `vite.dev.config.ts`
  (`forge/docker_manager.py`), the boot step after generation (`boot()` in
  `forge/docker_manager.py`, its call in `forge/cli/main.py`), `forge doctor`,
  the updater's handling of both (`forge/sync/forge_to_project/updater/`),
  `forge.toml` and the quality recipe
- Related: [plan](../../plans/2026-10-26-frontend-dev-server.md);
  [[ADR-006]] (provenance), [[ADR-007]] (separate frontend templates),
  [[ADR-011]] (UI kit option, the precedent for a field of the frontend),
  [[ADR-012]] (a list of frontends)

## Context

In a generated stack the `frontend` service is nginx over a built bundle.
The only way to see a change in the running stack is to rebuild the frontend
image, which takes minutes per edit. A developer who wants hot reload runs
`npm run dev` on the host, which is a second origin: it has no Traefik, so no
`/api/<backend>` routes, and none of the stack's hostnames.

A downstream project built a dev overlay by hand and ran it on Linux.
What it learned shapes this decision:

- Dependencies must be installed for the container's platform. A host
  `node_modules` holds the host's native binaries (esbuild, rolldown,
  lightningcss).
- Files the dev server writes into a bind-mounted `src/` are owned by the
  container user. The Vue template writes `src/auto-imports.d.ts`, and with
  Nuxt UI `src/components.d.ts`, at startup, so a root container leaves root-owned
  files in the checkout. Docker also creates a bind source that does not exist
  as `root`.
- The base `docker-compose.yml` carries values an overlay must replace, not add
  to: the nginx.conf volume and the port-80 Traefik label. Compose merges
  volumes by target and labels by entry, so omission deletes nothing.
- The base `frontend` service has **no** health check. The overlay adds one;
  a slim Node image has no `wget` or `curl`.
- Vite refuses an external hostname unless it is admitted. It admits `localhost`,
  `*.localhost` (so `app.localhost` already passes) and IP addresses on its own, and
  any host named in `__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS`; the wrapper lists
  `app.localhost` anyway so the host Traefik routes here is stated in the project.
- No Content-Security-Policy applies, because nginx is not in the path.

## Decision

1. **A per-frontend field, Vue first.** `frontend.dev_server` (boolean,
   default `false`) is a `FrontendConfig` field, travelling the way `ui_kit`
   does (ADR-011): the stack file, `--dev-server`, `[forge.frontend]` in
   `forge.toml`, the recipe. The interactive generator asks on Vue only. Asking for it
   without a Vue frontend (`--dev-server` or the stack key with no
   `--frontend vue`) is an error, never a silent no-op. `forge.toml` records it
   only when on, so a project without it keeps the manifest it had; the recipe
   always holds the field. It is not an option-registry
   path. It applies to Vue first; Svelte follows in a separate change with the
   same tests; Flutter is refused. The overlay overrides the compose service of every frontend that has the
   field (ADR-012): `frontend` for the primary, on `app.localhost`, and a further
   app's DNS name on its own host, each on its own `server_port`.
2. **The Dockerfile gains a `dev` stage; nginx stays last.** With the field
   on, the stages are `deps → source → dev` and `source → builder → nginx`,
   where `source` is the single place that runs `COPY . .`, so the dev and
   production images cannot diverge in what the generated Dockerfile copies.
   `COPY . .` does not copy a named external build context: a project that
   adds `COPY --from=<context>` instructions to its scaffold-owned Dockerfile
   (a project that copies shared libraries this way) must place them in `source` for
   the dev image to have them, which the sidecar merge on enabling the field
   makes explicit. The overlay sets only `build.target`, so the base file's
   `context`, `dockerfile` and `additional_contexts` are inherited. With the field off the rendered Dockerfile is
   unchanged, byte for byte, for npm and yarn. For pnpm the comment and the default change, with the
   field on or off: the image installed `pnpm@latest`, whose newest major refused a
   lockfile the host's pnpm had just written (`minimumReleaseAge`). The cause is that the
   image's pnpm is not the one that wrote the lockfile, so a new project now records the
   host's pnpm in `package.json` as `packageManager`, which corepack honours; the
   Dockerfile's `pnpm@10` is only the default for a project without that field. `dev` runs as a non-root user, and the application
   tree, including Vite's cache directories, is writable by that user; the
   files arrive with `COPY --chown`.
3. **A generated `docker-compose.dev.yaml` overrides each enabled frontend's service.**
   It sets the build target to `dev`, the dev image's own name (the base file
   builds the nginx image under the service's default name, and one tag for both
   would let a later `up` without `--build` start the wrong one),
   `VITE_API_BASE_URL` to empty (the project's `.env.development` points the
   browser at the dev server's own port, which nothing publishes here, so the
   page calls the API on its own origin through the router), a Node-based
   health check, and the one Traefik label that differs. The `dev` stage's
   `CMD` runs the `vite` binary with `--host 0.0.0.0` and
   `--config vite.dev.config.ts`, not the project's `dev` script, because a flag
   in that script (`--port 3000`) would win over the wrapper's settings. The
   image runs without Compose too. The overlay replaces
   what nginx needed: `volumes: !reset []` and `healthcheck: !override`. It does
   not replace the labels. Labels merge key by key, so the overlay sets only
   `traefik.http.services.frontend.loadbalancer.server.port` to the frontend's
   `server_port`, and the router's rule, entrypoint and any host another overlay
   added stay as they are. (The first version overrode all labels and wrote
   forge's own back; applied to a project whose router rule named two hosts it
   narrowed the rule to `Host(app.localhost)`.)
   A test merges a downstream overlay's extra host and expects it to survive.
   The minimum is **Docker Compose 2.39.4**: `develop.watch.initial_sync` is
   listed in that release's notes (docker/compose#13232), and it is later than
   `!override` (2.24.4) and `!reset` (2.24), which forge's Compose boot test
   already needs. `forge doctor` reports an older Compose.
4. **Compose Watch is the sync mechanism, not a bind mount.** The overlay
   declares `develop.watch`:
   - one `sync` rule, with `initial_sync: true`, from the frontend's directory
     to `/app`, ignoring `node_modules`, build output, coverage artifacts, the
     package manifest and the lockfile, and the generated declaration files
     (`src/auto-imports.d.ts`, `src/components.d.ts`). New and deleted files
     follow. No `sync+restart`: Vite restarts itself when its config, what the
     config imports, or an `.env` file changes, and a Compose restart on top
     of that restarted the server twice (observed);
   - a `rebuild` rule for `package.json` and a separate one for the package
     manager's lockfile.
   The stack is started with `docker compose -f docker-compose.yml -f
   docker-compose.dev.yaml up --build --watch`. Sync is one way: files the dev
   server generates stay in the container, so no host file is created by it
   and the host needs no uid mapping. Dependencies are in the image's `deps`
   layer, so no install volume, no one-shot install service and no
   `--ignore-scripts` workaround exist.
5. **A wrapper Vite config, never an edit of the project's own.**
   `apps/<frontend_slug>/vite.dev.config.ts` resolves the base `vite.config.ts` whether
   it exports an object, a function of `ConfigEnv`, or a promise, spreads it, and
   then sets `server.host` to `0.0.0.0`, `port` to the frontend's `server_port`, `strictPort`,
   and `allowedHosts` to the frontend's generated Traefik hostname,
   `app.localhost`, read from the same place the Compose template reads it. It
   never sets `allowedHosts: true`. The four keys are set after the project's
   config and not merged into it: `mergeConfig` concatenates host lists and keeps
   `true`, so a project's own `allowedHosts` would otherwise widen the list (the
   e2e test sets `allowedHosts: true` in the project and expects `evil.example`
   refused). The guarantee is that arbitrary external hostnames are refused and
   that the project's config cannot widen the list. It is not that only
   `app.localhost` answers: Vite always admits `localhost`, every `*.localhost`
   name and IP addresses, and adds hosts named in its
   `__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS` environment variable. That is
   enough here because Traefik routes only `app.localhost` to this service and
   the Vite port is not published. A project that routes more hosts to the
   frontend (an overlay that adds `app.localtest.me`, say) names them in
   `FORGE_DEV_ALLOWED_HOSTS` on the frontend service, a comma-separated list of
   plain host names that the wrapper checks and appends after `app.localhost`;
   without it Vite answers 403 for those hosts, which a trial in an existing
   project showed. File polling is off: Compose writes the
   synced files inside the container, where Vite's watcher sees them (measured:
   an edit reaches the browser in under a second). It imports the project's
   config with its `.ts` extension, which Vite 8's native config loader asks for.
   The HMR WebSocket stays on the page's own origin; Traefik proxies it.
   `tsconfig.node.json` includes the wrapper.
6. **The dev frontend behaves like the built one with respect to sign-in, and
   forge does not fix that.** The generated gatekeeper defines only the
   ForwardAuth middleware and declares no router
   (`forge/features/auth/templates/platform_auth_gatekeeper/compose.yaml`), so
   the stack routes none of its browser paths (`/auth/login`, `/callback`,
   `/auth/userinfo`, `/logout`) on the frontend host, and the backend routers
   require a session the browser cannot obtain. With `include_auth` on the
   generated `.env` sets `VITE_AUTH_DISABLED=false`, so the app redirects to
   `/auth/login` in the dev stack exactly as in the built one. The overlay does
   not override that variable: `VITE_AUTH_DISABLED=true` would only swap in the
   development user, and every API call would still be refused at the gate.
   What keeps Keycloak working for a project that routes the gatekeeper itself
   (a project's own overlay may) is that the dev server stays on the **same origin**:
   the same host and port behind Traefik, no published Vite port, so the
   session cookie, the realm's redirect URIs (`http://*.localhost/*`,
   `http://localhost:*`) and `webOrigins` need no change. The Vite dev proxy
   the template generates targets `http://localhost:<port>` and is never
   reached, because Traefik's backend routers match `/api/<backend>` first. A
   forge-only dev loop with no sign-in uses a project generated with
   `include_auth` off. The docs say all of this.
7. **The Dockerfile, overlay and wrapper are scaffold, and the updater treats
   their removal explicitly.** They are non-source or root-level files under
   `ownership()`, so an edited copy receives a `.forge-merge` sidecar on update
   and an untouched copy is rewritten. Vanished scaffold is normally left on
   disk (ADR-006); for these files that would leave a stale overlay after
   `dev_server` goes `true → false`. On every update, while forge no longer
   writes the overlay or a wrapper, the updater deletes it once it matches its
   recorded digest, and reports one that was edited (`left_in_place`); it keeps
   an edited one recorded as forge's own, so turning the option on again meets a
   sidecar instead of a collision, and it never touches a file the project owns
   (`origin` user) under the same name. The Dockerfile falls back to the
   single-stage form through the normal sidecar path.
8. **Bind mounts are a documented, best-effort fallback.** On Compose 2.39.4
   or newer, where Watch's sync is too slow or a filesystem does not suit it,
   generated docs say how to mount `src/` into the `dev` target instead. An older
   Compose cannot use the overlay at all.
   Only paths that exist are mounted (the Vue template tree has no `public/`,
   though a rendered app can; Svelte's static assets are `static/`), and the docs name the caveats: on Linux the
   container writes as its own uid, and the generated declaration files then
   appear in the checkout owned by that uid. It is not a second generated mode.
9. **Forge does not boot the dev stack itself.** After generation `boot()`
   runs `docker compose up --build` on the base file only, which would serve
   nginx and not Vite. With `dev_server` on, forge skips the automatic boot
   (whether from `--yes` or the interactive prompt) and prints the Watch
   command, so no run claims a hot-reload stack it did not start.
10. **Dev is not the production proof.** No CSP applies, nginx is absent, and
   the sign-in paths are not routed. Production Compose and Helm tests, and the CSP lane against the
   production build, remain the gates for those behaviours; the docs say so.

## Alternatives considered

### The prototype's bind mount of the whole checkout, with install volumes

Rejected as the default. It needs two named volumes for installs, a one-shot
service that chowns volume mount points, another that installs under a
read-only mount with `--ignore-scripts`, and a uid variable, and it still
cannot run on a host whose files the container user cannot write. Compose
Watch removes all of it by keeping dependencies in the image and syncing in
one direction. The prototype is the fallback in the docs, not the design.

### A registry option (`frontend.dev_overlay`)

Rejected. The value describes how one frontend is built and run, and `ui_kit`
already established the `FrontendConfig` route for a setting of that kind
that changes what is rendered.

### Always emit the overlay and the `dev` stage

Rejected. Every project's Dockerfile, recipe and golden snapshots would change
for a feature most projects do not use, and the overlay would have to live in
every update. An option keeps the off state's Dockerfile and Compose output
unchanged. The recipe records the field, so the goldens change; that is the
accepted cost, as in ADR-011.

### A compose profile in `docker-compose.yml`

Rejected. A profile can add a service but cannot replace the nginx service's
build, volumes and labels, so it would need a second frontend service on a
second host.

### A host dev server with a Vite proxy to Traefik

Rejected. It is the second-origin setup the Context describes.

## Consequences

- With the field off, the rendered Compose files are unchanged, and so is every
  other generated file. The Dockerfile is too, except that a pnpm project's installs
  pnpm 10 instead of `latest` when `package.json` names no `packageManager` (the dev
  stage needed it; the production build shares the line), and a newly generated pnpm
  project's `package.json` records the host's pnpm. `.forge/quality.json` gains
  `dev_server = false` on the next update, so the recipe and the golden
  snapshots that hash it change; `forge.toml` records `dev_server = true` only
  for a project that enables it.
- Projects on Docker Compose older than 2.39.4 cannot use the overlay.
- A change under `package.json` or a lockfile rebuilds the image and restarts
  the service; a change to the Vite config restarts it without a rebuild.
- The host does not see the generated declaration files the dev server writes.
  An editor's types follow a host install or build.
- A project that edits the generated Dockerfile receives a sidecar the first
  time it enables the field.
- Traefik's Docker provider reads every container on the daemon. A dev stack
  and another stack that route `Host(app.localhost)` on one daemon collide on
  the router name `frontend`; stop the other stack, or constrain this Traefik
  to its own Compose project (`TRAEFIK_PROVIDERS_DOCKER_CONSTRAINTS`). The same
  holds for the built stack.
- A project that adopts the option has its own follow-ups. It moves its
  `COPY --from=<context>` lines into `source` and decides which of its
  additional build contexts are watched; an unwatched one needs a rebuild, as
  today. Vite aliases that point outside the frontend root resolve to paths
  outside `/app` in the container, and Vite refuses to serve files outside
  `server.fs.allow`. The wrapper merges the project's own `vite.config.ts`, so
  the project widens `server.fs.allow` there; forge's wrapper knows no project
  paths and adds none. A Compose stack assembled through `COMPOSE_FILE` would
  lose its other overlays under the two-file command above; the dev overlay
  goes last in that list.
- Svelte keeps the nginx flow until its parity change lands.
- With several frontends the overlay is one file with a service per app that has
  the option; turning the option off for one app removes that app's wrapper, and
  the overlay goes when no app has it.

## References

- [Implementation plan](../../plans/2026-10-26-frontend-dev-server.md)
- `forge/templates/deploy/docker-compose.yml.j2`, `Dockerfile.node.j2` and
  `forge/docker_manager.py` — what the overlay replaces
- Docker Compose Watch, `https://docs.docker.com/compose/how-tos/file-watch/`
- Docker Compose merge rules (`!reset`, `!override`),
  `https://docs.docker.com/reference/compose-file/merge/`
