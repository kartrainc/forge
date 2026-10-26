# Forge: a list of frontends per project

Base: c05544c4 (`kartrainc/forge` main, PR #3).

Date: 2026-10-25. Status: implemented on `feat/multi-frontend`, 2026-10-26.

Requested scope: let a project declare several frontends the way it declares
several backends, so a second generated app (an operator console, for example)
is built, deployed, recorded and updated like the main one. Forge learns no
product names, hosts or count. Decision record:
[ADR-012](../docs/architecture-decisions/ADR-012-a-list-of-frontends.md).

## Decisions

| Area | Choice |
| --- | --- |
| Stack file | `frontend:` or `frontends:`, both refused, like `backend:`/`backends:` |
| Config | `ProjectConfig.frontends`; `frontend` is the first entry (property + setter) |
| Primary | the first entry keeps every name and host it had (`apps/frontend`, `app.localhost`, compose `frontend`, Helm `frontend:`) |
| New key | `name` on a frontend: `apps/<name>`, the package name, the host label `<name>` |
| Pages | the existing `features`: absent = every backend feature, `[]` = none, a list = those |
| Kit, sign-in | `ui_kit` per frontend (already); one realm and client per project |
| Placement | a fragment or component lands in every app whose framework and kit can take it, components by their whole closure |
| Deploy | a compose service/router and Helm Deployment/Service/Ingresses per app; backend routes on every app host |
| Gatekeeper | `GATEKEEPER_FRONTEND_HOST_LABELS` resolves further app hosts to the project's realm and tenant |
| Records | recipe `frontends`; `forge.toml` v5 `[[forge.frontends]]` with two or more apps |

## Changes, by commit

1. `feat(config)`: the list in `ProjectConfig`, `FrontendConfig.name` and
   `features: list | None`, validation of names, hosts and pages, the builder's
   `frontends:` and its refusal of unit flags beside a list (backends too), the
   recipe round trip.
2. `feat(generate)`: per-app contexts, rendering, Dockerfile, nginx, CSP,
   checks and codegen; per-app fragment and component placement in generate,
   `--update` and `--plan-update`; the resolver's any-app gating; the Svelte
   template accepts `features: []`; one quality subject per app;
   `scripts/check_generated_quality.py --extra-frontend`.
3. `feat(deploy)`: topology entries and the backend host rule; compose services;
   Helm `frontends:` values, `templates/frontends.yaml`, the gatekeeper env and
   NOTES; the gatekeeper's host-label fallback.
4. `feat(sync)`: manifest v5, every re-stamper carrying the list, the updater
   rebuilding every recorded app, template tasks per app and per backend, the
   recipe at version 2, and the owned update refusing a moved primary.
5. `docs`: this plan, ADR-012, CHANGELOG, UPGRADING, the `multi-frontend.yaml`
   example, the `py_vue_svelte_frontends`
   matrix scenario (port base 8220: two Vue apps and a Svelte app without
   pages) and a two-app generated-quality lane.

## Tests

- `tests/test_multi_frontend_config.py`: model, builder, validation, recipe.
- `tests/test_multi_frontend_generate.py`: two and three apps rendered dry,
  per-app pages, kits, codegen, `--json`, the plan header.
- `tests/test_frontend_fragment_placement.py`,
  `tests/test_update_component_placement.py`,
  `tests/test_capability_resolver.py` (`TestSeveralFrontends`): placement and
  kit gating, with the patch-scoped mutation gate on the resolver passing.
- `tests/test_deploy_topology.py`, `tests/test_docker_manager.py`,
  `tests/test_deploy_helm_validation.py` (helm lint/template and kubeconform),
  `tests/test_gatekeeper_frontend_hosts.py`,
  `tests/runtime/gatekeeper_contract.py`.
- `tests/test_manifest.py`, `tests/test_multi_frontend_update.py` and the
  re-stamper suites: v5 records, update and plan-update, template tasks.
- Goldens: the one-frontend presets change only in the recorded recipe
  (every preset) and, in `full_feature_max`, the gatekeeper's `config.py` and
  `tenant_config.py`.
- Follow-up PR: drop the `ProjectConfig(frontend=...)` shorthand, move the
  remaining tests to `frontends=`, and add a `multi_frontend` golden preset.
- `tests/matrix/test_runner_diagnostics.py`: the verify lane builds and labels
  every app.

## Open points

- The generated compose routes none of the gatekeeper's browser paths on any
  app host and `/auth/login` redirects to the Docker-internal Keycloak URL;
  both pre-date this work and are left to the project's edge.
- The gatekeeper keys rate limits by host label, so each app host has its own
  bucket (ADR-012, Consequences).
- Pre-existing defects found during the work, left for follow-up PRs: the
  primary's Helm Ingress rewrites `/` with the API paths; the update path for
  projects without a recipe drops `include_keycloak` (uninstalling the auth
  fragments) and uninstalls every generated component (`forge.toml` does not
  record `components`); `--plan-update` ignores the recorded option origins;
  `_RESERVED_SERVICE_NAMES` misses some compose keys; the `--json` report gives
  backend directories without `services/`; the root `multi-backend.yaml`
  example collides with the gatekeeper's port 5000.
