# Capabilities and limitations

This page describes the checked-in implementation. Use `forge --version`, the
[live option catalog](../FEATURES.md), and a resolved plan to establish support
for your installed version and plugins. Historical fragment counts and proposed
future releases are not a capability guarantee.

## Supported targets

| Surface | Available | Boundaries |
| --- | --- | --- |
| Backends | Python/FastAPI, Node/Fastify, Rust/Axum, Go/net/http with chi | Feature and specialized application-template coverage differs by backend. |
| Frontends | Vue, Svelte, Flutter, or none | Frontend framework support does not imply parity of every component/feature. |
| Layouts | sidebar, topnav, tabbar, threepane, bento, docs | Available combinations are validated by the layout registry. |
| Layered components | Vue basic components and application templates; composition graph | Vue is the current framework target; no standalone Layer-2 seed is shipped. |
| LLM providers | OpenAI on Python/Node/Rust/Go; Anthropic/Ollama/Bedrock on Python | A provider SDK existing upstream does not mean Forge ships its adapter. |
| Vector-store/RAG stack | Python adapters | Node/Rust/Go applications can call a separately integrated Python retrieval service. |
| MCP platform feature | Python | Select `platform.mcp` only where the resolver supports it. |
| Queue port | Backend-specific adapters selected by `queue.backend` | Inspect adapter semantics and language support; delivery guarantees still require application behavior. |
| Project options | One option map per project | Differing per-service requirements may need separate generated configurations. |
| Runtime agent modes | `none`, `llm_only`, `tool_calling` subject to feature support | `multi_agent` is registered but rejected as unimplemented. |

Inspect a candidate with `forge --config stack.yaml --plan --json`. The
[recommender](../guides/technology-selection.md) filters languages against service
requirements and preserves explicit compatible choices; it is not a benchmark
or a distributed topology optimizer.

## Authentication and service boundaries

Application services call dependencies directly. Generated S2S grants and SDKs
provide credentials; they do not generate business workflows, network policies,
or endpoint permission rules. JWTs currently use one shared platform audience;
service-specific scopes and resource authorization remain receiver obligations.

`auth.api_keys` is disabled by default and requires Gatekeeper. It provides scoped,
expiring integration keys and create/list/revoke APIs. A management UI, automatic
rotation and per-key quotas are not generated. Roles do not automatically become
scopes. Already-issued bearer tokens retain their validity until expiry after a
key is revoked; configure revocation enforcement if a shorter window is needed.
See [API keys](../guides/api-keys.md) and [deployment](../operations/deployment.md).

## Validation boundaries

- **Architecture:** independent regeneration protects generic runtime and checks
  supported static dependency/patching patterns. It is not a sandbox or proof
  against arbitrary reflective runtime behavior. Review recipe/workflow changes.
- **Coverage:** all three required suites must execute passing tests; their union
  must cover strictly more than 80% of new executable lines per application and
  shared package. An unavailable base, missing reports, or stale sources fail.
- **Browser E2E:** Vue/Svelte quality tests run a real browser. Their fixtures do
  not establish a deployed backend/login/tenant flow; add full-stack journeys for
  your configuration.
- **Flutter:** code generation, analysis, and the LCOV adapter are implemented.
  The native device/E2E coverage path was not validated in the quality rollout
  ([PR #350](https://github.com/cchifor/forge/pull/350)); static analysis is not
  coverage evidence. The earlier Flutter analyzer `xfail` is no longer present
  in `tests/e2e/test_full_generation.py`.
- **Configuration matrix:** CI checks selected backend/frontend/preset scenarios,
  not the entire feature cross-product. New combinations may need credentials,
  test issuers, database services, or additional fixtures.
- **Enforcement:** generated workflows do not configure downstream repository
  protection. Make the generated quality job required in the target repository.

See [generated-code quality](../operations/generated-code-quality.md) and the
[validation matrix](../operations/validation-matrix.md) for the exact layers.

## Upgrade and development limits

`forge --quality migrate` reports an ownership proposal; migration requires a
reviewed regeneration and adoption. Existing customizations are not automatically
accepted as generic runtime. Incremental service/entity helpers predate the full
recipe transaction: a topology/schema change must keep config, manifest, recipe,
output, and test inventory consistent. Use a separate candidate generation for a
reviewable migration, as described in [customization](../guides/customization.md).

Generated infrastructure contains development credentials and deterministic
service secrets. Replace them and validate ingress, trust, database roles,
backups, and observability before deployment; see [deployment](../operations/deployment.md).

Cold Rust builds and first-time frontend/Flutter toolchain setup can be slow.
Use the workflow's native caches and time budgets when reproducing failures.
Generator unit/integration CI covers Linux, macOS, and Windows; Docker/native
rendered quality lanes are primarily Linux. Do not infer a deployed-platform
certification from a generator OS test.

## Planned and historical material

[RFC-005](../rfcs/RFC-005-polyglot-ports.md) records deferred polyglot-port work;
other RFCs may be proposed or partly implemented. Their status and
[implementation plans](../plans/README.md) explain intent, while the installed
registry and source establish present behavior. Old benchmark numbers, CI
snapshots, and audit reports should retain their original date and context.

Report reproducible gaps with the Forge version, config without secrets, failing
command/status, and native logs at the
[issue tracker](https://github.com/cchifor/forge/issues).
