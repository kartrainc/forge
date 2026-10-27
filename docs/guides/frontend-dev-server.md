# Hot reload for the Compose frontend

By default the `frontend` service in `docker-compose.yml` is nginx over a built
bundle, so seeing a change means rebuilding the image. For the Vue template,
`frontend.dev_server` swaps it for a Vite dev server behind the same Traefik
router and host (`app.localhost`): an edit to a file under `apps/<frontend>/`
reaches the open page in about a second, without a rebuild or a reload.

The decision and its alternatives are in
[ADR-013](../architecture-decisions/ADR-013-a-dev-server-overlay-for-the-compose-frontend.md).

## Turn it on

In the stack file:

```yaml
frontend: { framework: vue, dev_server: true }
```

or `forge --dev-server ...` when generating a project. Vue only; forge refuses it
for Svelte and Flutter. The option is off by default. An existing project sets
`"dev_server": true` in `config.frontend` of `.forge/quality.json`, the one place an
update reads, and runs `forge --update`; `forge --update --dev-server` has no effect
(see [UPGRADING](../../UPGRADING.md#frontend-dev-server-unreleased)).

With several frontends (ADR-012) set it on each app that wants it; the overlay then has a service per app, on that app's port and host.

It adds these:

- a `dev` stage in `apps/<frontend>/Dockerfile`, which runs as the unprivileged
  `node` user;
- `docker-compose.dev.yaml` at the project root;
- `apps/<frontend>/vite.dev.config.ts`, which wraps your own `vite.config.ts`
  (an object, a promise or a function of `ConfigEnv`), listens on all
  interfaces, refuses to move off its port, and lists `app.localhost` as its host.
  The wrapper prevents your own `allowedHosts` from widening the list; Vite's
  built-in allowances (`localhost`, `*.localhost`, IP addresses) and any host in the
  `__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS` environment variable still apply. The
  router sends only `app.localhost` here and the port is not published;
- an `environment` entry on the overlay service that empties `VITE_API_BASE_URL`: the
  generated `.env.development` points the browser at the dev server's own port, which
  nothing publishes here, so the page calls the API on its own origin, through the
  router, as in the built stack;
- its own image name for the dev image (`<project>-frontend-dev`), so a later `up`
  without `--build` cannot start the dev image in the nginx service or the reverse.

The container runs the `vite` binary with the wrapper, not your `dev` script, so a
flag in that script (`--port`, `--host`) does not apply here.

## Run it

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yaml up --build --watch
```

Needs Docker Compose **2.39.4 or newer** (`forge doctor` checks it). Forge never
starts this stack for you: its automatic boot runs `docker-compose.yml` alone,
which serves nginx, so with the option on it prints the command above instead.

What `--watch` does:

- the project directory is copied into the container as it changes, new and
  deleted files included, except `node_modules`, `dist`, `coverage`,
  `package.json`, the lockfile and the declaration files Vite generates
  (`src/auto-imports.d.ts`, `src/components.d.ts`). The `.dockerignore` applies to
  the sync as it does to the image build: `.env.local`, `.env.*.local` (so
  `.env.development.local`, the usual local Vite override), `build/`, `test-results/`
  and `playwright-report/` never reach the container;
- a change to `package.json` or the lockfile rebuilds the image;
- Vite restarts itself when `vite.config.ts`, what it imports, or an `.env` file
  changes.

Dependencies are installed in the image, for the container's platform, so a
host `node_modules` is never used. Sync is one-way: what the dev server writes
stays in the container and nothing in your checkout is owned by another user.
The consequence is that the editor's types for the generated declaration files
come from a host install or build, not from the running container.

## What it does not cover

- **No content-security policy.** nginx is not in the path, so the policy it
  serves is not applied. Check security headers on the built image
  (`docker compose up --build` without the overlay).
- **No sign-in.** The generated stack routes none of the gatekeeper's browser
  paths (`/auth/login`, `/callback`, `/auth/userinfo`, `/logout`), in the built
  stack or here. With `include_auth` on, the app redirects to `/auth/login` as it
  does in the built stack. For a hot-reload loop with no sign-in, generate the
  project with `include_auth: false`. A project that routes the gatekeeper itself
  in its own Compose overlay gets sign-in in the dev stack too: the dev server
  stays on the same origin, so the session cookie and the realm's redirect URIs
  need no change.
- **Other stacks on the same Docker daemon.** Traefik reads every container on
  the daemon. Another stack that routes `Host(app.localhost)` collides with this
  one on the router name `frontend`. Stop it, or point this stack's Traefik at its
  own Compose project with `TRAEFIK_PROVIDERS_DOCKER_CONSTRAINTS`.

## What has been run

The end-to-end test (`tests/e2e/test_dev_server_hmr.py`) runs npm, pnpm and yarn with the
shadcn kit, and npm with Nuxt UI, all with sign-in off: it hot-reloads a component through Traefik, refuses an external
Host header, checks that Vite's cache is writable, and that a `package.json` change
replaces the container. It ran on Linux (WSL2 with Docker Desktop), and a project on
a Windows drive was driven by hand with the Windows `docker.exe` (edits, CRLF files,
new and deleted files, a `package.json` rebuild). macOS and a native Windows install
of forge itself are unverified.

pnpm needed one fix, which the production build shares: the image installed `pnpm@latest`, and the newest major refused a lockfile the
host's pnpm had just written (its `minimumReleaseAge` policy), so the Dockerfile now
installs pnpm 10. Yarn needs a host Node that satisfies the project's engines.
Sign-in on has not been run at run time.

## Hosts other overlays add

The wrapper lists `app.localhost`. If another Compose overlay adds hosts to the
frontend's router rule, Vite answers 403 for them until you name them on the
frontend service, as a comma-separated list of plain host names:

```yaml
services:
  frontend:
    environment:
      FORGE_DEV_ALLOWED_HOSTS: app.example.test,app.other.test
```

A name with a scheme, port, path or wildcard stops the dev server at start-up.
The overlay keeps the router's other labels, so the hosts themselves are yours.

## Aliases outside the app

The container holds `apps/<frontend>` at `/app`. A `vite.config.ts` that aliases
into a sibling directory (`../../libs/...`) resolves outside Vite's root, and Vite
answers 403 for files outside `server.fs.allow`. Put the directories your
`COPY --from=` lines add to the image in the `source` stage of your Dockerfile
(`COPY . .` does not copy a named build context), and widen `server.fs.allow` in
your `vite.config.ts`; the wrapper merges it.

## If Watch is too slow

Compose Watch is the supported way to sync. If it is too slow on your filesystem,
you can bind-mount instead. This is best-effort and not covered by forge's tests.
Run the stack without `--watch` and add an override of your own that mounts the
paths you edit, only ones that exist:

```yaml
services:
  frontend:
    volumes:
      - ./apps/frontend/src:/app/src
```

Two caveats. On Linux the container user is uid 1000, so the declaration files Vite
writes into the mounted `src/` are owned by that uid in your checkout. And on
Docker Desktop for Windows or macOS, file-change events may not reach the
container: set `server.watch.usePolling` in your `vite.config.ts`. A change to
`package.json` needs `up --build`.

## Turn it off

Set `dev_server: false` and run `forge --update`. Forge removes
`docker-compose.dev.yaml` and `vite.dev.config.ts` if you have not changed them
(a changed file stays, the update lists it under `left_in_place`, and it stays
recorded, so turning the option on again does not collide with it), and the
Dockerfile loses its `dev` stage, as a `.forge-merge` sidecar if you edited the
Dockerfile.
