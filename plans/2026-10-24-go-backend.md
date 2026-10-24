# Forge: Go as a fourth built-in backend

Base: 58a17f1a1d874be764dca7399886d832af5a2b29 (`kartrainc/forge` main).

Date: 2026-10-24. Status: proposed.

Requested scope: support Go services the way Python, Node and Rust are
supported: a base service template, the platform-auth SDK and middleware, the
tier-1 fragments, the generated-quality gates, Compose/Helm output and CI.

## Decisions

| Area | Choice |
| --- | --- |
| Language value | `go`, `BackendLanguage.GO`, label "Go (chi)" |
| Version field | `go_version`, choices `1.27`, `1.26` (goose, go-redis and openai-go require 1.26) |
| HTTP | `net/http` handlers on the `go-chi/chi/v5` router (route groups, `Mount`, RFC-007 404s) |
| Database | `pgx/v5` pool, hand-written tenant-scoped SQL |
| Migrations | `goose/v3` over embedded `migrations/*.sql`, run by `cmd/migrate` |
| Config | RFC-008 layers: `config/*.yaml` + `.secrets.yaml` + `APP__*` env |
| Logging | `log/slog` JSON, `X-Request-Id` correlation |
| Shared SDK | `packages/platform-auth-go`, wired by a `replace` directive |
| Queue fragment | `queue_asynq` (Redis), the Go counterpart of `queue_bullmq` / `queue_apalis` |

## Generated service

```text
services/<name>/
├── go.mod / go.sum
├── cmd/server/main.go          scaffold
├── cmd/migrate/main.go         scaffold
├── internal/
│   ├── app/                    scaffold, FORGE markers for middleware and routes
│   ├── config/ errors/ identity/ db/
│   ├── middleware/             generated (correlation, CORS, body limit, recover)
│   ├── ports/ adapters/        fragment seams
│   ├── models/ data/repositories/ services/ routes/
├── migrations/                 embedded SQL
├── config/                     defaults / development / production / testing
├── tests/{unit,integration,e2e}/
└── Dockerfile
```

## Quality gates

| Step | Go adapter |
| --- | --- |
| lock | `go mod tidy` |
| install | `go mod download` + `go mod verify` |
| unit / integration | `go test -json -covermode=atomic -coverpkg=./... ./tests/<suite>/...` |
| e2e | as above, plus `cmd/*` built with `-cover`; `GOCOVERDIR` merged with `go tool covdata` |
| report | coverprofile converted to LCOV, module path mapped to the service directory |
| inventory | every non-test `.go` file with a function body |

## Phases

1. Language core: enum, spec, toolchain, template, Compose, generated CI,
   quality adapters, recipe/updater plumbing, recommender.
2. Auth: `platform_auth_sdk_go`, `platform_auth_go_middleware`, `auth.mode`
   wiring, contract parity runner.
3. Fragments: the 21 tier-1 fragments plus `queue_asynq`; Go joins the parity
   set so tier 1 means four languages.
4. Codegen and domain emitters, Helm, CI lanes, matrix scenarios, docs.

## Open points

- `examples/forge-go-backend` registered `go` through `add_backend`, which now
  collides with the built-in. It registers `go-stdlib` instead and keeps
  exercising the plugin-language paths.
- Upstream: propose to `cchifor/forge` through an ADR before the fork diverges.
- Local validation runs in Docker (`golang:1.27`); the host has no Go toolchain.

## Router choice

Measured 2026-10-24 (Go 1.27.3, arm64, same route, three middlewares, JSON body):

| Router | ns/op (4 CPU) | B/op, allocs | Binary | Extra modules |
| --- | --- | --- | --- | --- |
| net/http `ServeMux` | ~1200 | 2176, 20 | 5.8 MB | 0 |
| chi v5.5.0 | ~1290 | 2865, 23 | 6.0 MB | 1 |
| Gin v1.12.0 | ~1170 | 2273, 20 | 12.8 MB | 19 |
| Echo v4.16.2 | ~1170 | 2145, 20 | 6.2 MB | 10 |

A pooled Postgres `SELECT 1` costs ~47.5 µs on the same host, so router cost is
below 0.3% of the cheapest query. chi keeps every handler and middleware a plain
`http.Handler`, which the fragments and `otelhttp` compose with directly.
