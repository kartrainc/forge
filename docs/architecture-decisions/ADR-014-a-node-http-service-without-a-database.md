# ADR-014: A Node HTTP service without a database

- Status: Proposed
- Author: forge team
- Date: 2026-10-27
- Scope: the app-template manifest's `uses_database`
  (`forge/backend_app_templates.py`), the Node `http-service` manifest
  (`forge/templates/services/node/http-service/template.toml`), the Node
  template's `use_database` switch (`copier.yml`, `forge/variable_mapper.py`),
  `BackendConfig.features` defaults and validation (`forge/config/_backend.py`,
  `forge/cli/builder.py`), the app template's recovery on update
  (`forge/sync/forge_to_project/updater`), the database-owning backends in
  the topology, Compose, `init-db.sh` and Helm (`forge/config/_topology.py`,
  `forge/docker_manager.py`), shared enums and entities
  (`forge/codegen/pipeline.py`), the fragments that skip it, and the
  generated-quality lanes
- Related: [[ADR-001]] (hexagonal layering), [[ADR-004]] (module-level
  registries), [[ADR-006]] (provenance), [[ADR-013]] (the dev-server overlay)

## Context

Every Node backend was a `crud-service`: Prisma, a migrate one-shot, a
database URL, a database in `init-db.sh` and the sample `items` entity. A
stateless service, such as one that renders HTML into images or PDFs or
adapts an external API, had to generate all of it and then delete it by
hand. The deleted files came back as sidecars on every `forge --update`,
because forge still owned them, and the stack still created a database and
ran a migration for the service.

Python already has app templates beside `crud-service` (`worker`,
`tenant-management-service`), discovered from `template.toml` manifests.
`database_mode: none` exists too, but it is project-wide: one stateless
service beside database-backed ones cannot use it.

## Decision

1. **An app template can own no database.** A `template.toml` manifest may
   declare `uses_database = false` (a boolean; a malformed value skips the
   manifest, like any malformed manifest). `backend_uses_database()` answers
   for a backend's language and app template, and an unknown template owns a
   database, as every backend did before.
2. **Node's `http-service` is the Node template rendered with
   `use_database=false`,** not a copy of it. The switch excludes Prisma, the
   data layer, the sample entity's routes, schemas and services and their
   tests, and drops the database settings, the readiness check's database
   component, the Prisma scripts and dependencies, and the migrate step from
   the entrypoint. The generator passes the key only when it is false, so a
   `crud-service` renders and records exactly what it did before.
3. **The stack wires no database for it.** Compose renders no migrate
   one-shot and no database URL, `init-db.sh` creates no database, and the
   Helm workload has an empty `migrateCommand`. Postgres is rendered only for
   a backend that owns a database, or for Keycloak; with Keycloak alone, its
   database is the image's default `postgres`. Shared enums and entity types
   are emitted only into backends that own a database.
4. **No CRUD features.** Without `features`, an `http-service` backend
   serves none (a `crud-service` still defaults to `items`). Naming features
   for it is an error: the frontend would get pages for an API the service
   does not serve. The default is resolved when the app template is known, so
   it holds for a stack file, the CLI and a `BackendConfig` built in code,
   including one whose template is set after construction.
5. **Fragments that need state skip it:** the connection pool, background
   tasks, the queue port and BullMQ, and the cache port with its memory and
   Redis adapters, through `excluded_app_templates`, as the HTTP middleware
   skips `worker`.
6. **Its image keeps only production dependencies.** Nothing runs the Prisma
   CLI at start-up, so the builder prunes dev dependencies before the runtime
   stage copies `node_modules`. A `crud-service` image keeps them, because its
   entrypoint runs the migration.
7. **It is tested as a generated project.** The generated-quality workflow
   gains a Node `http-service` lane, and `scripts/check_generated_quality.py`
   takes `--app-template`.
8. **`forge --update` keeps it.** The updater reads each backend's app
   template back from its `.copier-answers.yml`: the template recorded as
   `_src_path`, and `use_database: false` for a template without a database,
   as it already reads the port. A service that matches no single template
   stays a `crud-service`. The same recovery keeps a Python `worker` and
   `tenant-management-service` what they are.

## Alternatives considered

- **A separate Node template directory without Prisma** (as Python's
  `worker` is self-contained). Rejected: the HTTP baseline (config, logging,
  health, error envelope, auth hooks) is the same, and two copies would
  drift. `worker` differs in its whole runtime; `http-service` differs only in
  what it leaves out.
- **A two-stage variant** (a base plus an overlay, as
  `tenant-management-service` does). Rejected: an overlay adds and replaces
  files but cannot remove the base's Prisma files, so it would need delete
  semantics in the renderer.
- **`database_mode: none` per service.** Rejected: the database mode is a
  project-wide option, and forge options are project-wide by design. The app
  template already describes a service's shape.
- **Keep the `items` default and ignore it.** Rejected: the frontend's
  generated pages and the end-to-end suite would target routes that do not
  exist.
- **Record the app template in `forge.toml`.** Rejected: it changes the
  manifest's format for a value each service's answers file already carries,
  and projects generated before the change would still need the recovery.

## Consequences

- A `crud-service` backend renders as before; only a backend that names
  `http-service` gets the database-free shape.
- Only Node has the template. Python, Rust and Go can add theirs with the
  same manifest key.
- The same change gives every Node service a `.dockerignore`, a
  `tests/integration/setup-env.ts` with the platform-auth defaults, an
  entrypoint E2E test and an auth-aware network test, so a service with
  platform auth passes its own suites outside the stack; UPGRADING lists the
  sidecars this produces.
- Running Node services from source in the stack is not part of this
  decision and is not planned. If a project needs it, it extends ADR-013's
  opt-in overlay to Node backends, in a change of its own.

## References

- [UPGRADING: Node HTTP service](../../UPGRADING.md#node-http-service-unreleased)
- `forge/templates/services/node/http-service/template.toml`
- `forge/templates/services/node-service-template/copier.yml` (`use_database`)
- `tests/test_backend_app_templates.py`
