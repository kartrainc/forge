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

or `forge --dev-server ...`. Vue only; forge refuses it for Svelte and Flutter.
The option is off by default, and an existing project turns it on with the same
`forge --update` as any other option (see
[UPGRADING](../../UPGRADING.md#frontend-dev-server-unreleased)).

It adds three things:

- a `dev` stage in `apps/<frontend>/Dockerfile`, which runs as the unprivileged
  `node` user;
- `docker-compose.dev.yaml` at the project root;
- `apps/<frontend>/vite.dev.config.ts`, which wraps your own `vite.config.ts`
  (an object, a promise or a function of `ConfigEnv`), listens on all
  interfaces, refuses to move off its port, and lists `app.localhost` as its host.
  The wrapper prevents your own `allowedHosts` from widening the list; Vite's
  built-in allowances (`localhost`, `*.localhost`, IP addresses) and any host in the
  `__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS` environment variable still apply. The
  router sends only `app.localhost` here and the port is not published.

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
  (`src/auto-imports.d.ts`, `src/components.d.ts`);
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

The end-to-end test (`tests/e2e/test_dev_server_hmr.py`) runs npm with the shadcn kit
and sign-in off: it hot-reloads a component through Traefik, refuses an external
Host header, checks that Vite's cache is writable, and that a `package.json` change
replaces the container. It ran on Linux (WSL2 with Docker Desktop), and a project on
a Windows drive was driven by hand with the Windows `docker.exe` (edits, CRLF files,
new and deleted files, a `package.json` rebuild). macOS and a native Windows install
of forge itself are unverified.

pnpm and yarn render correctly (stages, `CMD`, corepack location) but have not run:
the image's install step for pnpm, which the production build shares, is rejected by
the newest pnpm for a lockfile generated on the host, and yarn project generation can
fail at the lint step on some hosts. Both predate this option; until they are fixed,
treat the dev server as verified with npm.

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
(a changed file stays, and the update lists it under `left_in_place`), and the
Dockerfile loses its `dev` stage, as a `.forge-merge` sidecar if you edited the
Dockerfile.
