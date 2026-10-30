# Forge: a page-free application shell for the Vue frontend

Base: c03f337f (`kartrainc/forge` main, PR #15).

Date: 2026-10-29. Status: implemented on `feat/frontend-app-shell`, 2026-10-29 (see Deviations).

Requested scope: let a project generate only the common boilerplate of a Vue
frontend, choose its application template and its layouts, and declare its
menus (with sub-menus) and routing in the configuration, without forge
generating CRUD or sample pages; let it keep authentication screens, or an
authentication runtime, of its own. The first consumer is `kartrainc/kartra-ai`
(its plan `docs/plans/2026-10-29-console-app-shell-revamp.md`), whose console
follows a product design with a full-width header above the sidebar. Forge
learns no product names, routes or menus. Decision record:
[ADR-015](../docs/architecture-decisions/ADR-015-frontend-application-templates-layouts-and-menus.md).

## Decisions

| Area | Choice |
| --- | --- |
| Application template | `frontend.app_template`: `crud-app` (default; today's output) or `app-shell` (boilerplate, shell, no pages) |
| Kit and framework | `app-shell` with `framework: vue` and `ui_kit: nuxt-ui` only; the other frameworks and kits refuse the new keys |
| Pages | `app-shell` refuses `features` other than `[]` and emits nothing under `src/features/`, which stays the project's |
| Layouts | `layouts:`, a mapping of named layouts, each with a `shell`, its menus and an optional `placeholder` route; the first shell is `header-sidebar`; `layout` keeps its default with `app-shell` |
| Menus | per layout `primary`, `account`, `footer`; sections with an optional label; items with `id`, `label`, `icon`, one of `to`, `action`, `children` (one level), an optional `badge` key; no destination = placeholder |
| Routing | `routing:` `landing`, `sign_in`, `not_found`, `auth_stuck`, `forbidden`, each `builtin` or a project route name |
| Authentication | `auth.runtime: forge \| project`, `auth.session_dialog: builtin \| project`; the guards depend on a contract the project passes in |
| Seam | generated `src/shared/application/`, `src/shared/layouts/`, `src/shared/system/`, `src/shared/pages/`; scaffold `main.ts`, `App.vue`, `src/app/router/index.ts`; generated code imports no project code |
| Extras | `extras:` for `app-shell`: `home`, `profile`, `settings`, `health`, `confirm`, `list_helpers`, `forms`, `capabilities`, `event_stream`, `layout_helpers`, `msw`; `crud-app` keeps them all |
| Observability | unchanged: the `observability.*` options and `include_sentry` |
| Stack file | the new keys are read from `frontend:` and `frontends[i]`; no CLI flags (a menu does not fit one) |
| Update | an unchanged scaffold file forge stops emitting is deleted; an edited one stays recorded and is reported |
| Corrections | chat codegen and `src/core/ui-protocol` only with `include_chat`; reserved names checked against emitted pages |

## Configuration

```yaml
frontend:
  framework: vue
  ui_kit: nuxt-ui
  app_template: app-shell
  include_auth: true
  auth: { runtime: forge, session_dialog: builtin }
  extras: []
  routing:
    landing: orders              # a project route
    sign_in: builtin
    not_found: builtin
    auth_stuck: builtin
    forbidden: builtin
  layouts:
    console:
      shell: header-sidebar
      placeholder: not-built     # a project route taking :item
      menus:
        primary:
          - items:
              - { id: overview, label: Overview, icon: i-lucide-house }
              - id: sales
                label: Sales
                icon: i-lucide-shopping-cart
                children:
                  - { id: orders, label: Orders, icon: i-lucide-receipt, to: orders }
                  - { id: invoices, label: Invoices, icon: i-lucide-file-text, badge: invoices }
          - label: Settings
            items:
              - { id: team, label: Team, icon: i-lucide-users }
        account:
          - items:
              - { id: account, label: Account settings, icon: i-lucide-user, to: account }
              - { id: sign-out, label: Log out, icon: i-lucide-log-out, action: sign-out }
        footer:
          - items:
              - { id: support, label: Support }
```

Validation (`forge/frontend_app.py`, called from `FrontendConfig.validate`, so
on generation and on every recipe read):

- `app_template` is a registered template; `app-shell` requires Vue, `nuxt-ui`,
  `features` absent or `[]`, `layout` at its default, at least one layout;
  `layouts`, `routing`, `auth` and `extras` are refused with `crud-app`.
- Layout names, menu ids and action ids are kebab-case; ids are unique within a
  layout; an item has at most one of `to`, `action`, `children`; children are
  one level deep and carry no `children`; labels are non-empty; `icon` matches
  `i-<collection>-<name>`; `shell` is a registered shell that takes the kit.
- `routing` has all five keys; each value is `builtin` or a route name
  (`[a-z][a-z0-9-]*`); `sign_in`, `auth_stuck` and `session_dialog` need
  `include_auth`.
- `auth.runtime` and `auth.session_dialog` take their two values; `extras`
  names known extras, once each.
- Route names in `to`, `placeholder` and `routing` are checked against the
  composed routes at runtime by the router factory, not at generation:
  generation cannot see the project's routes.

The values travel to the template as one JSON answer (`application`), the way
`backend_features` does, and are rendered into `src/shared/application/config.ts`.
The recipe stores them as plain mappings; `read_recipe` passes them through
`FrontendConfig(**item)` unchanged.

## What `app-shell` emits

| Path under `apps/<name>/` | Ownership | When |
| --- | --- | --- |
| `src/app/main.ts`, `src/app/App.vue` (`UApp`, the router view, the query-error toasts) | scaffold | always |
| `src/app/router/index.ts` (one call of the router factory, empty project contributions) | scaffold | always |
| `src/app/providers/{csp,head,query-client}.ts` and their tests, `src/app/styles/main.css`, `src/custom/theme/*` | scaffold | always |
| `src/shared/api/{client,errors}.ts`, `src/shared/composables/useApiClient.ts`, `src/shared/config/runtime.ts`, `src/shared/observability/*` | generated | always |
| `src/shared/application/{contracts,config,router,guards,menus}.ts` | generated | always |
| `src/shared/layouts/HeaderSidebarShell.vue` and its regions under `src/shared/layouts/shell/` | generated | a layout uses `header-sidebar` |
| `src/shared/system/{NotFound,AuthStuck,Forbidden,SignIn}Page.vue` | generated | the contract is `builtin` |
| `src/shared/composables/{useAuth,useSessionTimeout}.ts` | generated | `auth.runtime: forge` |
| `src/shared/system/SessionTimeoutDialog.vue` | generated | `auth.session_dialog: builtin` |
| `src/shared/pages/{Home,Profile,Settings}Page.vue`, the `health`, `confirm`, list, form, capability, event-stream and layout helpers, `src/shared/mocks/*` | generated | the matching extra |
| unit tests beside the generated modules, `tests/integration/application.test.ts`, `tests/e2e/application.spec.ts`, `tests/e2e/{session,health}.ts`, `src/test/setup.ts` | scaffold | always |
| `package.json`, `vite.config.ts`, `tsconfig*.json`, `eslint.config.js`, the Vitest and Playwright configs, `index.html`, `Dockerfile`, `nginx.conf`, `scripts/serve-csp.mjs` | scaffold | always |

Not emitted by `app-shell`: anything under `src/features/`, `MainLayout.vue`,
`AuthLayout.vue`, `AppSidebar.vue`, `AppHeader.vue`, the CRUD and hub machinery
of `post_generate.py`, `ui.store.ts`, `useSidebarShortcut.ts`, `types/index.ts`,
`src/core/ui-protocol/`, the chat codegen outputs, the README's sample rows.

## Runtime

The router factory (`src/shared/application/router.ts`):

```ts
createApplicationRouter({
  auth,                  // AuthContract: isAuthenticated, isLoading, user, hasRole(role), signIn(redirect?), signOut()
  routes,                // Record<LayoutName, RouteRecordRaw[]>: the project's pages per layout
  publicRoutes,          // routes outside any layout (a project sign-in page, for example)
  layouts,               // Partial<Record<LayoutName, Component>>: wrappers that fill a shell's slots
  actions,               // Record<ActionId, () => void | Promise<void>>: menu actions such as sign-out
  activeLayouts,         // LayoutName[]: default every layout; a project chooses by host
  history,               // default createWebHistory()
})
```

- It mounts each active layout at its root (the first at `/`, the others under
  `/<name>` unless the project gives them their own hosts), with the wrapper or
  the shell as the component and the project's routes as children; adds the
  public routes and the built-in system routes; installs the guards; and throws
  when a menu item, a placeholder or a routing contract names a route that the
  composition does not contain, or when two routes share a name.
- `useApplicationMenu(layout, menu)` returns Nuxt UI `NavigationMenuItem[][]`:
  `to` resolved to the route, placeholders to `{ name: placeholder, params: { item } }`,
  actions to `onSelect`, sections' labels as `type: 'label'` items, the active
  item and its open parent from `meta.menu` or the matching `to`, badges from
  `provideMenuBadges(values)`.
- The guards wait for the contract's `isLoading` (5 s, then `auth_stuck`), send
  an unauthenticated visit to `sign_in` with the redirect, a signed-in visit of
  `sign_in` to `landing`, a missing `meta.requiresRole` to `forbidden`, and set
  the document title from `meta.title`.
- `auth.runtime: forge` adapts `useAuth` to the contract (`login` and `logout`
  become `signIn` and `signOut`); the scaffold router passes it. With `project`,
  the scaffold router imports the contract from `src/custom/auth/contract.ts`, a
  starter written once that throws until the project implements it.
- The `header-sidebar` shell renders `UDashboardGroup`, `UDashboardSidebar` and
  `UDashboardPanel` under a full-width header, with slots `logo`, `header`,
  `actions`, `footer` and `aside`, the collapse state of the sidebar and the
  aside in local storage, the aside sliding over the page on narrow screens and
  the search slot covering the header there, and the edge toggles.

## Changes, by commit

1. `feat(config)`: `FrontendConfig.app_template`, `layouts`, `routing`, `auth`,
   `extras`; `forge/frontend_app.py` with the template and shell registries and
   the validation; the stack intake for `frontend:` and `frontends[i]` reads
   them from the file (the single-table getter learns file-only keys); recipe
   round-trip; the legacy (no-recipe) updater refuses `app-shell`.
2. `feat(vue)`: `app_template` and the `application` JSON answer in
   `variable_mapper.vue_context`; conditional names on the `crud-app` files;
   `post_generate.py` skips features, hubs, the home API patch and the sample
   README rows for `app-shell`.
3. `feat(vue)`: `src/shared/application/` (contracts, configuration, router
   factory, guards, menus) with unit tests; the `app-shell` `main.ts`, `App.vue`
   and router; the integration and e2e starters.
4. `feat(vue)`: the `header-sidebar` shell and its regions, registered for
   `nuxt-ui`.
5. `feat(auth)`: the built-in system pages; `auth.runtime` and
   `auth.session_dialog`; the contract's adapter over `useAuth`; the
   session-timeout fragment placed per app, only with `auth.runtime: forge`, its
   dialog under `src/shared/system/` in `app-shell`.
6. `feat(vue)`: the extras as conditional emission, the sample pages under
   `src/shared/pages/` with their routes registered by the factory.
7. `fix(update)`: discontinued scaffold files deleted when unchanged, reported
   and kept recorded when edited (the rule of `is_dev_server_file`, generalised).
8. `fix(codegen)`: `ui_protocol.gen.ts`, `events.gen.ts` and
   `src/core/ui-protocol/` only in apps with `include_chat`.
9. `fix(config)`: reserved names checked against the pages a frontend emits.
10. `test`: the `app_shell` golden; the recipe hash refreshed in the existing
    goldens; the generated-quality lane `vue/app-shell`; a matrix scenario with
    two layouts; structural tests.
11. `docs`: ADR-015 and its index row; the frontend-layouts guide (application
    templates, shells, menus, routing, authentication); the `forge-platform`
    skill; CHANGELOG; UPGRADING (`crud-app` to `app-shell`, and the UI kit's
    switch-back, whose leftovers are now deleted); the plans index.

12. `fix(config,update)` (review of #16): looping and malformed app-shell settings
    refused; discontinued files taken back only on positive evidence, never beside
    a pending `.forge-merge`; the no-auth `useAuth.login` takes the redirect.
13. `fix(vue)` (review of #16): the guards, routes, menus and bootstrap fixes of the
    Deviations' review section, each with its failing test first; the reference
    configuration and the e2e starter open a sub-menu route and a placeholder; a
    `vue/app-shell-no-auth` lane.
14. `build`: Node 26 everywhere and the current frontend and Node service
    libraries (the owner's ruling).

## Tests

- **Configuration:** valid and invalid layouts, menus (duplicate ids, two
  destinations, nested children, bad icons), routing values, authentication
  values and extras; `app-shell` refusing features, a layout, the shadcn kit,
  Svelte and Flutter; `crud-app` refusing the new keys; the YAML intake through
  `frontend:` and `frontends:`; the recipe round-trip.
- **Rendering (dry run):** the `app-shell` file set (nothing under
  `src/features/`, no `MainLayout`, sidebar, mocks or chat outputs); each extra
  adds exactly its files; `auth.runtime: project` emits neither `useAuth` nor
  `useSessionTimeout`; built-in and project system pages; `crud-app` byte for
  byte as before, the recipe excepted.
- **Rendered app** (`vue/app-shell` lane): `npm ci`, the unit, integration and
  e2e suites each executing tests, coverage above 80 %, the content-security
  check; the e2e starter boots the app, lands, opens a placeholder, a sub-menu
  item and the not-found page.
- **Router factory:** composition per layout, public routes, active layouts,
  the guards' five destinations, the errors for missing routes and duplicate
  names, menus with badges, actions and the active item.
- **Update:** a scratch `crud-app` project moved to `app-shell` (unchanged sample
  files deleted, an edited page reported and kept recorded); the UI kit's
  switch-back; a project file never touched.
- **Corrections:** no chat outputs without `include_chat`; a backend feature
  named `settings` accepted when no frontend emits that page.

## Upgrade from `crud-app`

1. Set `app_template: app-shell`, `features: []`, the layouts, menus, routing,
   authentication and extras in the stack file and in the recipe's
   `config.frontend` (the primary's record; `frontends[0]` is its mirror).
2. Run the owned update. It deletes the sample files the project never edited
   and lists the edited ones as left in place.
3. Move what the project still needs out of the files left in place, delete
   them, and compose the project's routes, wrappers, actions and authentication
   contract in `src/app/router/index.ts`.
4. Run the gates: architecture, the three test tiers, coverage, the
   content-security check.

## Deviations

What the implementation does differently from the sections above, and why.

1. **Layouts are stored as a list.** The stack file keeps the mapping of name to
   layout, but `FrontendConfig.layouts` (and the recipe) hold a list of
   `{"name": ..., ...}` entries: the recipe is written with sorted keys, which
   reordered a mapping and changed which layout is mounted at `/`. The rendered
   configuration also orders every item's keys itself, so a regeneration from
   the recipe matches the architecture gate byte for byte.
2. **Only `routing.landing` is required.** The four system destinations default
   to `builtin`; `sign_in` and `auth_stuck` are ignored without `include_auth`
   while `builtin`, and refused as project routes.
3. **The session-timeout dialog keeps the fragment's path.** The fragment writes
   `src/features/auth/components/SessionTimeoutModal.vue`; moving it for
   `app-shell` alone needs per-app path mapping in the fragment applier.
   `auth.session_dialog: builtin` mounts it from there, and only when the
   fragment reaches the app (`auth.mode` generate after the no-Keycloak
   coercion, `auth.runtime: forge`); otherwise the answer falls back to
   `project`. It is the one forge path under `src/features/` of an `app-shell`
   app.
4. **The project's auth starter is `src/app/auth.ts`.** With
   `auth.runtime: project` the scaffold module the guards, `main.ts` and the
   router import is itself the starter, instead of a file under
   `src/custom/auth/`, where a project may already keep its own sign-in code.
5. **`health` is the indicator only.** The health and info composables stay in
   `crud-app`'s home feature, whose API paths `post_generate.py` patches per
   backend.
6. **The post-generation task is split.** `crud-app` keeps its script byte for
   byte; `app-shell` gets a short one of its own under the same name, so the
   sample application's rendered output does not change.
7. **The chat correction is Vue's.** Svelte and Flutter keep their chat types
   whatever `include_chat` says; their templates do not delete the chat feature.
8. **No refusal in the legacy updater.** A project generated with `app-shell`
   always has a recipe, so the no-recipe path never meets one.
9. **Two additions.** `useApplicationDropdown` gives a menu as `UDropdownMenu`
   items (the shell's account menu), and `src/shared/application/testing.ts`
   holds the fixtures of the runtime's unit tests.

### After the review of #16

An adversarial review of the pull request found fourteen issues; each was reproduced
by a failing test first, then fixed. What changed against the sections above:

10. **Guards.** Every `meta.requiresRole` along the matched routes must hold; the
    deepest route that sets `meta.requiresAuth` decides; entering `auth_stuck` never
    waits again; no redirect goes to the route the visit is on.
11. **Routing.** `landing` may not be a system destination (the sign-in route, built
    in or not, the not-found, auth-stuck or forbidden route). The `home` extra sits
    at `/home`, so `/` always opens `landing`. A project route named as `not_found`
    gets a catch-all beside it that renders it and keeps the address.
12. **Bootstrap.** `main.ts` starts the auth contract without waiting for it; a
    sign-in that never answers ends on `auth_stuck` instead of a blank page.
13. **Menus.** The active item falls back to the item whose `to` is a matched route;
    the open sub-menu is controlled (`useOpenMenuItems`) and follows navigation;
    dropdown items keep their badges; a placeholder route without `:item` gets no
    params.
14. **Update.** A discontinued scaffold file is deleted only on positive evidence:
    recorded `scaffold`, origin `base-template` or `fragment`, still `scaffold` under
    the ownership policy, and its hash; anything else stays, keeps its record and is
    reported. A pending `.forge-merge` beside a discontinued file stops the update.
15. **Configuration.** Shapes are checked before defaults apply (`menus: []`,
    `auth: []`, `routing: []`, `extras: {}` are errors), and a layout body may not
    name another layout than its key.
16. **Without sign-in.** The no-auth `useAuth.login` takes the optional redirect,
    so the adapter type-checks; a `vue/app-shell-no-auth` lane renders it.
17. **Coverage.** The reference configuration has a placeholder route and a
    sub-menu item with a route, and the e2e starter opens both; it also checks that
    a stalled sign-in shows `auth_stuck`.

### Node 26 and the libraries

18. **The owner's ruling of round 2.** Node 26 everywhere: the frontend and e2e
    images, `engines`, the Node backend's default and choices (`26`, `24`, `22`,
    `20`, `18`), `@types/node` following `node_version` in the Node service, CI.
    The frontend and Node service libraries move to their current releases
    (CHANGELOG lists them); `@nuxt/ui` stays 4.12.0 and TypeScript ^6.0.3, since
    typescript-eslint's peer range ends below 6.1. `@tsconfig/node26` replaces
    `@tsconfig/node22` as a devDependency; no tsconfig of the template extends
    either, so the compiler options do not change.

### After the reviewers' round 1 on #16

The review bots found one routing gap, two runtime issues and five smaller ones;
each behaviour change was reproduced by a failing test first.

19. **Routing.** A project route named as `not_found` answers unknown addresses
    across the application: one top-level catch-all copies its chain of layout and
    parents, so a destination under a second layout or a nested route renders in
    them. Item 11's sibling catch-all only reached addresses under its parent.
20. **Guards.** A stalled sign-in without `auth_stuck` is judged as no sign-in, so
    a protected route stays closed.
21. **Configuration.** An explicit `auth.session_dialog: builtin` is refused where
    the generated auth stack is absent; left out, it falls back to `project`.
22. **Node.** Amends 18: the choices are `26`, `24` and `22`, since vitest 5 runs on
    `^22.12`, `^24` and `>=26`; the CLI's version flags take the registry's choices;
    the workspace root's `engines` follows the oldest Node service.
23. **Update.** The update names the files it takes back (text, JSON, a warning in
    the structured report); the sidecar check resolves its path like the others.
24. **ADR-015** lists the extras as implemented.

### The first consumer's cutover

Generating kartrainc/kartra-ai's console on `app-shell` (no extras, Kartra's own
sign-in page, a placeholder for its unbuilt items) found two gaps.

25. **Generated tests.** The router factory's tests assumed the `home` extra and the
    built-in not-found and forbidden pages; they now assert the pages the app's own
    configuration renders, so a project without them passes its generated tests.
26. **Menus.** On the placeholder route, the item its `:item` names is the active
    one, so the sidebar marks it and opens its parent, as for any other page.

### After the owner's review on #16

27. **Versions.** Amends 22: the Node template's Copier prompt drops `20` and `18`
    too, and a test holds every backend template's Copier choices and default to
    the registry's. It found Python's apart: forge offered `3.12` and `3.11`, which
    the template's Copier prompt refuses, so the registry offers its `3.13` and
    `3.14`.
28. **Generated tests.** The integration test's not-found case asserted the route's
    name, which a project destination's unnamed catch-all does not have; it now
    compares the views shown at the unknown address with the destination's own.
    The no-auth rendered lane names a page extra as `not_found`, so CI covers a
    project destination.

## Open points

- `layout` (one overlay of `crud-app`) and `layouts` (the named layouts of
  `app-shell`) are close names for different things; a rename of the old key
  belongs to a major release.
- A configuration-only change, such as a menu item, needs the whole update; a
  cheaper re-render of `src/shared/application/config.ts` alone is worth a
  follow-up.
- `public/canvas.manifest.json` is emitted for every app, chat or not; the first
  consumer's tests read it, so dropping it waits for that project.
- More shells (Nuxt UI's documented dashboard, where the sidebar holds the logo
  and each page its title bar) follow when a project asks; Svelte and Flutter
  are not planned.
- Menu labels are plain strings; translation is not covered.
