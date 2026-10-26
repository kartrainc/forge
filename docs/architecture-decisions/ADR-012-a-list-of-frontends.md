# ADR-012: A list of frontends

- Status: Accepted
- Author: forge team
- Date: 2026-10-26
- Scope: `ProjectConfig.frontends` and `FrontendConfig.name`/`features`
  (`forge/config/`), the stack file's `frontends:` (`forge/cli/builder.py`),
  per-app rendering (`forge/generator.py`, `forge/variable_mapper.py`,
  `forge/codegen/pipeline.py`), fragment and component placement
  (`forge/capability_resolver.py`, the updater's `_merge_driver.py`), the
  deployment topology, Compose and Helm output, the gatekeeper's host
  resolution, `forge.toml` schema v5 and the quality recipe. The manifest
  change follows ADR-006 and the v3 → v4 bump, which had no RFC either.
- Related: [plan](../../plans/2026-10-25-multi-frontend.md);
  [[ADR-006]] (provenance manifest), [[ADR-007]] (separate frontend templates),
  [[ADR-011]] (UI kit option)

## Context

A forge project had exactly one frontend: `ProjectConfig.frontend`, rendered
into the fixed `apps/frontend`. Backends have been a list since the
multi-backend work (`backend:` or `backends:` in the stack file,
`ProjectConfig.backends`, `backend` as the first entry). A project that needs a
second generated app beside its main one, an operator console for example,
could only copy a render into its own repository and guard it there, outside
the generator's provenance and update path.

## Decision

1. **Mirror the backend precedent.** The stack file takes `frontend:` (one app)
   or `frontends:` (a list); both is an error. `ProjectConfig.frontends` is the
   list and `frontend` a property returning the first entry, with a setter, as
   `backend` is. `ProjectConfig(frontend=x)` stays accepted for now as
   shorthand for `frontends=[x]`; a follow-up removes it, as `backend` has
   no such keyword.
2. **The first entry is the primary and keeps everything it has.** Compose
   service `frontend` on `app.localhost`, the Helm `frontend:` block and
   `<release>-frontend` resources, the root `tests/e2e` harness, the realm's
   SPA client, the recipe key `frontend`, `[forge.frontend]` in `forge.toml`
   and the `--json` keys stay the primary's. Further frontends are added beside
   it, never by restructuring it, so a project with one frontend keeps its
   apps, services and routes; only its records and two inert chart pieces
   change (UPGRADING).
3. **One new key, `name`.** It names the app's folder (`apps/<name>`) and
   package. The primary defaults to `frontend` and, renamed, moves its folder
   but keeps its compose service `frontend` and its host `app.localhost`;
   further entries default to `frontend-<i>` (`frontend_<i>` for Flutter) on
   port `5173 + i`. The name's DNS form (`_` → `-`, for Flutter's snake-case
   names) names a further app's compose service and router, its Helm resources
   and its host `<name>.localhost` / `<name>.<ingress.host>`. A name is a DNS
   label of at most 40 characters (snake case for Flutter); validation refuses
   duplicate names or hosts, a further app named `app` (the primary's host
   label), a backend's name, a reserved compose service name, the `-migrate`
   suffix, and `framework: none` beside other frontends.
4. **Pages come from the existing `features`.** Absent means every backend
   feature, as before; `[]` means no CRUD pages; a list means those pages,
   each of which must be a backend feature. The UI kit is already per
   frontend; sign-in stays project-level, every app using the project's realm
   and client.
5. **A fragment or component lands in every app that can take it.** Placement
   is per app: a fragment with `target_frontends` applies in each app whose
   framework it targets and, when it declares `ui_kits`, whose kit it lists; a
   component applies only where its whole closure renders on the app's kit. A
   root fragment still applies once. The resolver refuses only what no app can
   take.
6. **Every app is deployed and signs in on its own host.** Compose and Helm
   emit a service and route per app; backend routes answer on every app's
   host. The generated gatekeeper resolves the first host label of each
   further app (`GATEKEEPER_FRONTEND_HOST_LABELS`, derived from the hosts in
   the Helm chart) to its admin realm (`KEYCLOAK_ADMIN_REALM`) and default
   tenant, as it does for `app`, instead of a realm of the same name.
7. **Every app is recorded and updated.** `forge.toml` schema v5 adds a
   `[[forge.frontends]]` array, written with two or more frontends;
   `[forge.frontend]` keeps the primary. The recipe gains `frontends`. The
   updater, `--plan-update` and every manifest re-stamp carry the whole list;
   a template update re-renders each app of that framework.

## Alternatives considered

- **A `host` key per frontend.** Rejected: the host follows from the name,
  and the Helm values can still override it per app.
- **A `pages` key.** Rejected: `features` already says which CRUD pages an app
  has; it only needed `[]` to mean "none".
- **A Keycloak client or realm per frontend.** Rejected for now: one project,
  one realm and client; a separate identity boundary is a project decision the
  stack file does not need to model yet.
- **Restructuring the Helm `frontend:` block into a map.** Rejected: it would
  change every existing values file. Further apps get a `frontends:` map beside
  it and a new `templates/frontends.yaml`, which renders nothing with one app.
- **Conditional chart emission.** Rejected: the fragment applier would need new
  semantics for one file; the new template is inert for one frontend.
- **Components in the primary only.** Rejected: one placement rule for every
  frontend-targeted fragment, components included.
- **A guarded render inside the consuming project.** Rejected: it keeps the
  second app outside the generator's provenance, ownership and update path.

## Consequences

- `forge.toml` moves to schema v5. Forge reads v1–v4 as before (one frontend
  at most) and re-stamps to v5 on update; a v4 writer cannot record several
  frontends and refuses to. An older forge reading a v5 manifest sees only the
  primary. Callers that pin `schema_version == 4` must update.
- Each further app gets two Helm Ingresses: one for the `/api/<backend>` paths
  with `ingress.annotations`, one for `/` without them, because nginx-ingress
  applies an Ingress's `rewrite-target` to every one of its paths. The
  primary keeps `templates/ingress.yaml` unchanged.
- The gatekeeper keys rate limits, `X-Gatekeeper-Tenant` and the test bypass
  on the raw host label, as it does for `app`: each app's host has its own
  rate-limit bucket. Changing that would change every generated gatekeeper.
- A separate host is not an authorization boundary: the realm allows
  self-registration, so an operator app must rely on roles.
- Template updates now re-render every backend of a language, not only the
  first, which changes the behaviour of a one-frontend project with several
  backends of one language; that was a defect of the old update path.
- Pre-existing gaps left as they are: the generated Compose stack routes none
  of the gatekeeper's browser paths (`/auth/login`, `/callback`,
  `/auth/userinfo`, `/logout`) on any app host, and `/auth/login` redirects to
  the Docker-internal Keycloak URL; a project's edge configures both. The
  primary's `templates/ingress.yaml` puts `/` under the same `rewrite-target`
  as the API paths, so on nginx-ingress the app's assets are rewritten; that
  needs the same split, for every Kubernetes project, in a fix of its own.

## References

- [Implementation plan](../../plans/2026-10-25-multi-frontend.md)
- [`multi-frontend.yaml`](../../multi-frontend.yaml)
