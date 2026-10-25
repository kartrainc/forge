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
`cmd/migrate`) runs the embedded `migrations/*.sql` with goose against
`DATABASE_URL` (else `db.url`). `up` and `down` hold a Postgres session lock,
so concurrent runs wait instead of racing. `status` and `version` only read the
`goose_db_version` table: they take no lock, return while another run is
migrating, and never create the table.

| Command | Effect |
| ------- | ------ |
| `migrate` or `migrate up` | Apply every pending migration (compose and the Helm hook run this) |
| `migrate down` | Roll back the most recently applied migration |
| `migrate status` | Print each migration's version, state and applied time |
| `migrate version` | Print the highest applied version (`0` when none) |

```sh
# one-shot, before rolling out the app:
DATABASE_URL=... /app/migrate
# or, in compose, run the bundled one-shot migrate service:
docker compose run --rm <service>-migrate
docker compose run --rm <service>-migrate /app/migrate status
```

goose records applied versions, so re-running `up` is a no-op — safe on every
deploy. A migration that fails rolls back its own transaction; the ones `up`
applied before it stay applied.

| Exit | Log message | Cause |
| ---- | ----------- | ----- |
| 0 | — | The command succeeded |
| 1 | `configuration error` | The config files or `APP__*` variables are invalid |
| 1 | `database configuration error` | `DATABASE_URL` (or `db.url`) does not parse |
| 1 | `migration failed` | The database is unreachable or the command failed; `error` says why, e.g. a migration's SQL error, `no applied migration` or `no migration file for applied versions …` from `down` |
| 2 | `invalid migrate command` | An unknown command or an extra argument; checked before the configuration loads |

Every message goes through the service logger as a JSON line on stdout,
including `invalid migrate command` and `configuration error`, which are
logged at `info` level before the configuration loads; afterwards
`logging.level` and `logging.pretty` (text instead of JSON) apply. Nothing is
written to stderr, so read stdout or the container log, and expect secrets to
be redacted when `middleware.pii_redaction` is on.

### Migration numbering

| Versions | Owner |
| -------- | ----- |
| `00001`–`00099` | forge (base template and features) |
| `00100` and up | this project |

Add project migrations as `migrations/00100_<name>.sql`, `00101_<name>.sql`, …
with `-- +goose Up` and `-- +goose Down` sections. Out-of-order migrations are
allowed, so when a forge update adds a migration numbered below one the
database already has, `up` applies it instead of failing.

Every applied version must keep its file: never delete or renumber a
migration a database has applied; add a new one that reverses it instead.
`status` lists a recorded version without a file as `applied` with
`(no file)` in the migration column, and `down` refuses to run while any
exists, naming the versions (`no migration file for applied versions …`).
Restore those files to roll back.

`down` rolls back in the order migrations were applied, not by number, so
after an out-of-order `up` the migration it rolls back is not the highest
version.

## Row-level security (`database.multitenancy=shared_rls`)

This section applies only to a service generated with
`database.multitenancy=shared_rls`, which ships
`migrations/00099_enable_rls.sql` and `internal/tenancy`.

Each repository call runs in a transaction that binds the caller's tenant with
`set_config('app.current_tenant', <tenant uuid>, true)`; the
`tenant_isolation_<table>` policies compare that setting with the table's
`customer_id`. `00099` only covers the forge tables it lists that exist and
have a `customer_id` column — in this service, `items`.

Tables the project adds are **not** isolated until a migration numbered
`00100` or higher enables RLS on them. Replace `invoices` and `customer_id`
with the table and its tenant column:

```sql
-- +goose Up
ALTER TABLE invoices ENABLE ROW LEVEL SECURITY;
ALTER TABLE invoices FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_invoices ON invoices
    USING (customer_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
    WITH CHECK (customer_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid);

-- +goose Down
DROP POLICY tenant_isolation_invoices ON invoices;
ALTER TABLE invoices NO FORCE ROW LEVEL SECURITY;
ALTER TABLE invoices DISABLE ROW LEVEL SECURITY;
```

`FORCE` applies the policy to the table owner too. Superusers and `BYPASSRLS`
roles are never filtered, so run the service as a role that is neither. Run
the new table's queries through the same tenant scope as `items`
(`tenantScope(pool)` at `FORGE:REPOSITORY_SCOPE` in `internal/app/app.go`):
without a bound tenant the policy matches no rows.

Data migrations run without a tenant, so under `FORCE` an `UPDATE` or
`DELETE` on an RLS table affects 0 rows and an `INSERT` fails the policy
check. Bind each tenant inside the migration's transaction before its
statements:

```sql
-- +goose Up
SELECT set_config('app.current_tenant', '<tenant uuid>', true);
UPDATE invoices SET status = 'open' WHERE status IS NULL;
```

Repeat the pair per tenant, and keep the migration transactional (no
`-- +goose NO TRANSACTION`): the setting ends with the transaction. Otherwise
run that migration as a `BYPASSRLS` role.

`TENANT_RLS_GUC` must stay `app.current_tenant`: the policies read that name,
so with any other value the service binds a setting no policy reads, and
every scoped query matches no rows.

`migrate down` rolls back the most recently applied migration. Right after
`shared_rls` is enabled on an existing database, that is `00099`, even when
project migrations numbered `00100` or higher exist, because `up` applied it
last. Rolling it back drops the policies and disables RLS, which removes the
database-level tenant isolation; `migrate down` logs the warning
`row-level security rolled back` when it does. Run `migrate up` to restore it.

## Graceful shutdown

`cmd/server` cancels its context on `SIGINT` or `SIGTERM`; `app.Serve` stops
accepting connections and drains in-flight requests for up to 20 seconds,
logging `shutdown signal received — draining connections`. Set the container
termination grace period above your slowest request.

## Health checks

The service exposes `/api/v1/health/live` and `/api/v1/health/ready`. Wire
`/live` to the liveness probe and `/ready` (a database ping) to the readiness
probe so traffic only routes once dependencies are reachable.
