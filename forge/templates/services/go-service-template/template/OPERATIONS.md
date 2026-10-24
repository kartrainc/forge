# Operations runbook

Operator-facing notes for running this Go (net/http) service in production.
See `OBSERVABILITY.md` for tracing, metrics, and logging.

## Configuration order

Settings load lowest-to-highest priority (later wins):

1. Struct defaults (`config.Defaults`, in code).
2. `config/defaults.yaml`.
3. `config/{ENV}.yaml` — selected by `ENV` (or `APP_ENV`), default `development`.
4. `.secrets.yaml` — loaded if present.
5. `APP__*` environment variables — nested with `__`, e.g.
   `APP__DB__URL`, `APP__SERVER__PORT`. Booleans and numbers are parsed.

**Env vars always win.** The connection pool and the migrator prefer the
standard `DATABASE_URL` over `db.url`; `PORT` overrides `server.port`.

## Database migrations

Migrations are **not** run on server startup. The `migrate` binary (built from
`cmd/migrate`) applies the embedded `migrations/*.sql` with goose under a
Postgres session lock:

```sh
# one-shot, before rolling out the app:
DATABASE_URL=... /app/migrate
# or, in compose, run the bundled one-shot migrate service:
docker compose run --rm <service>-migrate
```

goose records applied versions, so re-running is a no-op — safe on every deploy.

## Graceful shutdown

`cmd/server` cancels its context on `SIGINT` or `SIGTERM`; `app.Serve` stops
accepting connections and drains in-flight requests for up to 20 seconds,
logging `shutdown signal received — draining connections`. Set the container
termination grace period above your slowest request.

## Health checks

The service exposes `/api/v1/health/live` and `/api/v1/health/ready`. Wire
`/live` to the liveness probe and `/ready` (a database ping) to the readiness
probe so traffic only routes once dependencies are reachable.
