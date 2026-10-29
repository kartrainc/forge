# ADR-015: Frontend application templates, with layouts, menus and routing from the configuration

- Status: Proposed
- Author: forge team
- Date: 2026-10-29
- Scope: `FrontendConfig` and its validation (`forge/config/_frontend.py`, a new
  `forge/frontend_app.py`), the stack intake (`forge/cli/builder.py`), the Vue
  template's answers (`forge/variable_mapper.py`), its conditional files and
  post-generation task (`forge/templates/apps/vue-frontend-template/`), the
  session-timeout fragment's placement (`forge/features/auth/`), the owned
  update's discontinued files (`forge/quality/update.py`), the chat codegen
  outputs (`forge/codegen/pipeline.py`), the reserved-name check
  (`forge/config/_project.py`), the golden snapshots and the generated-quality
  lanes
- Related: [plan](../../plans/2026-10-29-frontend-app-shell.md);
  [[ADR-006]] (provenance), [[ADR-010]] (layered components), [[ADR-011]]
  (the UI kit option), [[ADR-012]] (a list of frontends), [[ADR-014]] (the
  backends' `http-service` application template)

## Context

The Vue template renders a sample application around the boilerplate every
frontend needs. For each backend feature it writes a CRUD slice (list, create
and detail pages, an API composable, a schema, mock handlers, unit, integration
and e2e tests) and injects it into the router, the sidebar, the mock registry
and the home page. It always writes a home page with service health and quick
actions, profile and settings pages, a sign-in page, a `MainLayout` with a
hard-coded sidebar (Home, the CRUD items, Profile, Settings), and guards that
send users to the routes named `home` and `login`.

A project that builds its own product on the template fights that output.
kartra-ai removes the generated routes at runtime, takes over the names `home`
and `login`, mounts a second layout beside the generated one, keeps tests that
lean on the sample pages, and ships five CRUD pages that call endpoints no
service serves. `features: []` (ADR-012) removes the CRUD slices, but not the
sample pages, the hard-coded sidebar or the `home` contract.

Three couplings sit underneath:

- **The layout choice selects the whole template.** `layout: sidebar` *is* the
  base template; the other layouts are overlays that replace `MainLayout.vue`.
  "What the app contains" and "what the shell looks like" are one setting, a
  frontend has one shell, and only `sidebar` takes the Nuxt UI kit.
- **The shell cannot be the project's.** `src/shared/**` is `generated`: a
  project cannot change the sidebar's items, and a file it adds there fails the
  architecture gate. Nothing in the configuration describes a menu.
- **Authentication is all or nothing.** `auth.mode` is `generate` or `none` for
  the whole project. A frontend cannot keep forge's runtime while owning its
  sign-in screens, nor bring a runtime of its own while the backends keep the
  platform-auth SDKs.

The owned update adds a fourth: a scaffold file forge stops emitting stays on
disk, unrecorded and unreported, unless it belongs to the dev-server overlay.
Leaving the sample application therefore leaves its pages and tests behind.

Backends already separate these concerns: `app_template` chooses the service's
shape (`crud-service`, `http-service`), and the shape decides what is emitted.

## Decision

1. **Application templates for the frontend.** `FrontendConfig.app_template`
   chooses the application's shape. `crud-app` is today's output and the
   default; projects that do not set the key render every file as before.
   `app-shell` renders the boilerplate and a configurable shell, and no page of
   its own. The choice is made before rendering, through conditional file names
   in the one Vue template (as the UI kit does), never by rendering the sample
   application and deleting it. `app-shell` requires the Vue framework and the
   `nuxt-ui` kit, refuses `features` other than `[]`, and keeps `layout` at its
   default.

2. **Named layouts.** An `app-shell` frontend declares `layouts`, a mapping from
   a layout name to its `shell`, its menus and an optional placeholder route.
   Several layouts may share one app (a vendor console and a staff area on two
   hosts, for example); the project chooses which are active at start-up. The
   first shell is `header-sidebar`: a full-width header with slots for the logo,
   the centre and the actions; a collapsible sidebar that renders the primary
   menu; the page with a footer slot; a right-hand panel slot that collapses and
   slides over the page on narrow screens; and the two edge toggles. It is built
   from Nuxt UI's dashboard components and layout utilities, keeps its state in
   local storage, and imports no project code. A project fills the slots by
   wrapping the shell in a component of its own.

3. **Menus from the configuration.** Each layout may declare a `primary` menu
   (the sidebar), an `account` menu and a `footer` menu. A menu is a list of
   sections, each with an optional label; an item has an `id`, a `label`, an
   `icon` (an Iconify name), and at most one of `to` (a route name), `action` (a
   project action, such as signing out) and `children` (one level). An item
   with none of them is a placeholder and opens the layout's placeholder route
   with `{ item: <id> }`. `badge` names a key whose value the project supplies at
   runtime; counts are data, not configuration. A route names the item it
   belongs to in `meta.menu`, which marks the item active and opens its parent;
   without it, the item whose `to` matches is active. Changing a menu is a forge
   update.

4. **Routing contracts.** `routing` names five destinations: `landing`,
   `sign_in`, `not_found`, `auth_stuck` and `forbidden`. Each is `builtin`, and
   forge emits the page and its route, or the name of a project route. The
   guards use these destinations instead of the route names `home` and `login`:
   an unauthenticated visit goes to `sign_in`, a signed-in visit to `sign_in`
   goes to `landing`, a missing role goes to `forbidden`, a stalled sign-in to
   `auth_stuck`.

5. **An authentication contract.** The guards talk to a small contract (signed
   in, loading, user, has role, sign in, sign out) that the project passes to
   the router factory. `auth.runtime: forge` emits forge's `useAuth` and the
   session-timeout composable, which implement it; `auth.runtime: project` emits
   neither, and the project passes its own implementation. `auth.session_dialog`
   is `builtin` (forge mounts its dialog) or `project`. The backends' SDKs, the
   gatekeeper and the realm do not change.

6. **The seam.** Generated code never imports project code. Forge generates,
   under `src/shared/`, the application contracts, the resolved layouts, menus
   and routing, a router factory, the shells and the built-in system pages. The
   router factory takes the project's routes per layout, its public routes, its
   layout wrappers, its actions, its authentication contract and the active
   layouts, composes them once, and throws when a menu item, a routing contract
   or a placeholder names a route that does not exist. `main.ts`, `App.vue` and
   `src/app/router/index.ts` stay small scaffold composition roots, which the
   project edits.

7. **Extras.** What the sample application offers beyond the boilerplate is
   opt-in for `app-shell`, by name in `extras`: `home`, `profile` and
   `settings` pages, `health` (schemas, composables and the indicator),
   `confirm` (the confirmation host, dialog and composable), `list_helpers`,
   `msw` and `sentry`. `crud-app` keeps all of them.

8. **Discontinued scaffold files.** The owned update applies the dev-server
   rule to every scaffold file forge stops emitting: one unchanged since
   emission is deleted, one the project edited stays, keeps its record and is
   reported. A project file (origin `user`) is never touched.

9. **Two corrections on the way.** The chat codegen outputs (`ui_protocol.gen.ts`,
   `events.gen.ts`) are written only into apps with `include_chat`, and the
   reserved-name check looks at the pages a frontend emits, not at every
   backend feature.

## Alternatives considered

- **Page switches on today's template.** Turning off home, profile and
  settings is the smallest change, but the hard-coded sidebar, the single
  layout and the `home` contract stay, and so does the project's runtime
  surgery. Rejected.
- **Layer-3 component templates (ADR-010).** The `Console` template composes a
  page into `src/features/` and a routes array nothing wires in; it adds
  content to an application, it does not replace the application's shell. Not
  chosen; components remain available inside `app-shell` projects.
- **The shell as a scaffold starter.** Written once and owned by the project
  afterwards, it would let the project edit the sidebar freely, but menus would
  no longer follow the configuration and a forge release could not improve the
  shell. Rejected: the configuration is meant to describe the application.
- **Menus in a project file read at build time.** No regeneration for a menu
  change, but the configuration stops describing the application and forge
  cannot validate the menu against the routes it composes. Rejected for now;
  a cheaper regeneration for configuration-only changes is an open point of the
  plan.
- **A second Copier template for `app-shell`.** ADR-007 separates frameworks,
  not shapes of one framework's app; a second Vue template would duplicate the
  providers, the API client and the tooling. Rejected in favour of conditional
  file names, as the UI kit uses.

## Consequences

- A project that sets nothing renders every file it rendered before. The recipe
  records the new keys with their defaults, so the recipe's hash changes in
  every golden snapshot with a frontend; a new `app_shell` snapshot pins the new
  output, and a generated-quality lane renders it and runs its own suites.
- An `app-shell` app still meets the generated-quality floor (each of the unit,
  integration and e2e tiers executes tests, coverage above 80 % per subject):
  the template ships tests for the router factory, the menus, the guards, the
  shell and the system pages, which become the project's scaffold.
- Moving from `crud-app` to `app-shell` is an owned update: the unchanged sample
  files are deleted, the edited ones are reported and stay recorded, and the
  project composes its routes in `src/app/router/index.ts`. The UI-kit switch's
  documented behaviour changes the same way (unchanged files of the other kit
  are now deleted).
- The session-timeout fragment is placed per app, and only where
  `auth.runtime` is `forge`.
- Configuration-only changes, such as a menu item, need the update procedure;
  projects that change menus often feel that cost.
- Only the Vue template with the Nuxt UI kit learns the new keys; the other
  frameworks and kits refuse them, as they refuse `ui_kit: nuxt-ui`.

## References

- [Plan](../../plans/2026-10-29-frontend-app-shell.md)
- Nuxt UI's dashboard layout: `UDashboardGroup`, `UDashboardSidebar`,
  `UDashboardPanel`, `UNavigationMenu` (ui.nuxt.com, 4.12)
- The consuming project's plan: `kartrainc/kartra-ai`,
  `docs/plans/2026-10-29-console-app-shell-revamp.md`
