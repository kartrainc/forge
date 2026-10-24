# Cross-SDK parity contract

The `platform-auth` SDK ships in four languages (Python, Node, Rust, Go). Cross-language parity is the load-bearing claim: **the same JWT must yield the same `IdentityContext` (or the same `AuthError` variant) across all of them**.

This directory is the canonical scenario spec.

## Files

| File | Purpose |
| --- | --- |
| `scenarios.py` | Frozen dataclasses defining ~17 canonical scenarios. The `SCENARIOS` tuple is the contract. |
| `test_scenarios_well_formed.py` | Meta-test: gates the spec's internal coherence (unique names, valid error slugs, JSON-serializable, etc.). 15 tests. |
| `__init__.py` | Package marker + cross-references. |

## How runners consume this

Each language ships a runner alongside its SDK that reads the spec, mints a token per scenario, runs `AuthGuard.verify`, and asserts the outcome matches.

### Python runner (follow-up)

```
forge/tests/contract/auth_sdk_parity/test_python_runner.py
```

Imports `platform_auth.AuthGuard` (via sys.path manipulation against the forge-shipped SDK template), calls `platform_auth.testing.build_test_token(...)` per scenario, asserts outcomes.

### Node runner — vitest-based

The Node-side runner lives in the Node SDK at
`forge/features/auth/templates/platform_auth_sdk_node/.../test/parity_runner.test.ts`.

Loaded via the `PARITY_FIXTURES` env var pointing at the JSON dump
produced by `scenarios_as_json()`. Mints a token per scenario via
the SDK's testing helper (`buildTestToken` + `generateTestKeypair`),
runs each through `AuthGuard.verify`, and asserts the outcome
matches — including pinning the cross-language `AuthError.reason`
slug.

To run from inside a generated project (or from the SDK template
directory directly during forge development):

```bash
cd <project>/sdks/platform-auth-node
npm install

# Generate the canonical scenarios JSON.
python -c "
import json, sys
sys.path.insert(0, '<forge-repo>/tests/contract/auth_sdk_parity')
from scenarios import scenarios_as_json
print(json.dumps(scenarios_as_json()))
" > /tmp/scenarios.json

# Run the runner.
PARITY_FIXTURES=/tmp/scenarios.json npx vitest run test/parity_runner.test.ts
```

The runner skips itself when `PARITY_FIXTURES` is unset, so a plain
`npx vitest run` (during local SDK development) doesn't try to
exercise it.

**Forge-CI orchestrator**: `tests/contract/auth_sdk_parity/test_node_runner.py`
spawns `npx vitest run test/parity_runner.test.ts` from the SDK
directory with the scenarios JSON wired through `PARITY_FIXTURES`.
Skipped at collection time if `node`/`npx` aren't on PATH or if
`node_modules/` hasn't been installed (so forge's bare `pytest`
invocation stays green on dev machines without Node).

### Rust runner — `cargo test` integration test

The Rust-side runner lives in the Rust SDK at
`forge/features/auth/templates/platform_auth_sdk_rust/.../tests/parity_runner.rs`.

Gated behind `#![cfg(feature = "testing")]` so the bare-default
`cargo test` skips it; activated when the consumer (or forge's CI)
runs with the `testing` feature enabled. Loads scenarios JSON via
the `PARITY_FIXTURES` env var; uses `wiremock` to stand up a local
JWKS responder so the verifier's `reqwest::Client` resolves the
JWKS document without going to the network.

To run from inside a generated project:

```bash
cd <project>/sdks/platform-auth-rs

# Generate the canonical scenarios JSON.
python -c "
import json, sys
sys.path.insert(0, '<forge-repo>/tests/contract/auth_sdk_parity')
from scenarios import scenarios_as_json
print(json.dumps(scenarios_as_json()))
" > /tmp/scenarios.json

# Run the runner. Each scenario reports as a single test that either
# pass-asserts the IdentityContext fields or pins the AuthError
# variant + reason() slug.
PARITY_FIXTURES=/tmp/scenarios.json cargo test --features testing --test parity_runner -- --nocapture
```

The runner skips itself when `PARITY_FIXTURES` is unset, so a plain
`cargo test --features testing` doesn't try to exercise it.

**Forge-CI orchestrator**: `tests/contract/auth_sdk_parity/test_rust_runner.py`
spawns `cargo test --features testing --test parity_runner` from the
SDK directory with the scenarios JSON wired through `PARITY_FIXTURES`.
Skipped at collection time if `cargo` isn't on PATH (no
`npm install`-equivalent precondition — cargo resolves deps lazily
on first run, though the cold build is slow enough that the
orchestrator's timeout is set to 10 minutes).

### Go runner — `go test` parity test

The Go-side runner lives in the Go SDK at
`forge/features/auth/templates/platform_auth_sdk_go/go/files/packages/platform-auth-go/parity_test.go`.
It loads the scenarios JSON from `PARITY_FIXTURES` and skips itself when the
variable is unset; an `httptest` server serves the JWKS document.

```bash
cd <project>/packages/platform-auth-go
PARITY_FIXTURES=/tmp/scenarios.json go test -run "^TestCrossSDKParity$" -v .
```

**Forge-CI orchestrator**: `tests/contract/auth_sdk_parity/test_go_runner.py`
runs the same command from the SDK directory. Skipped at collection time if
`go` isn't on PATH.

## Adding a new scenario

1. Add a `Scenario(...)` entry to `SCENARIOS` in `scenarios.py`. Keep the `name` snake_case + verb-first.
2. If the scenario expects a new error slug not in `KNOWN_ERROR_SLUGS`, add it to:
   - `KNOWN_ERROR_SLUGS` in `test_scenarios_well_formed.py`
   - `ExpectedError` Literal in `scenarios.py`
   - Per-language SDK exception list in `test_features_auth_*_sdk.py`
3. Run `uv run pytest tests/contract/auth_sdk_parity/` — meta-tests must stay green.
4. Per-language runners pick up the scenario automatically on next invocation.

## Why a spec, not pre-baked fixtures

Pre-baked JWT fixtures couple to specific keypair material. A change to the test keypair would invalidate every committed fixture. The scenario spec carries *inputs* (the things `build_test_token` accepts); each runner mints its own JWT from the same inputs and verifies it. That keeps the spec self-contained, language-portable, and key-rotation-safe.

## Cross-references

- Implementation plan: `~/.claude/plans/review-the-c-users-chifo-work-platform-a-pure-torvalds.md` (Phase 9 deliverables).
- Architectural context: `forge/docs/auth-architecture.md`.
- Python SDK: `forge/features/auth/templates/platform_auth_sdk/python/files/sdks/platform-auth/`.
- Node SDK: `forge/features/auth/templates/platform_auth_sdk_node/node/files/sdks/platform-auth-node/`.
- Rust SDK: `forge/features/auth/templates/platform_auth_sdk_rust/rust/files/sdks/platform-auth-rs/`.
- Go SDK: `forge/features/auth/templates/platform_auth_sdk_go/go/files/packages/platform-auth-go/`.
