# Frontend layouts

A generated frontend's **app-shell layout** — the arrangement of navigation,
content, and panels — is selectable with `--layout` (CLI), `frontend.layout`
(YAML), or the interactive prompt. Every layout is fully responsive
(desktop / tablet / mobile) and is built by composing forge's reusable
Layer-1/Layer-2 components (see [Layered components](../../README.md#layered-components-vue-3)).

> **Scope.** All six layouts ship for **Vue 3, Svelte 5, and Flutter**, and every
> `(framework, layout)` builds in its generated container — Vue/Svelte via
> `npm run build`, Flutter via `flutter build web` (the Flutter image runs
> `dart run build_runner` for the freezed / riverpod / json codegen before
> building). Selecting an unavailable `(framework, layout)` pair fails config
> validation with the list of layouts available for that framework.

## The layouts

| Slug | Name | Regions | Best for |
|------|------|---------|----------|
| `sidebar` | Sidebar App Shell *(default)* | left nav rail + top bar + main (+ optional right panel) | Enterprise SaaS, admin, internal tools |
| `topnav` | Top-Nav Content Shell | top menu bar + centered main + footer | Marketing, e-commerce, content sites |
| `tabbar` | Tab Bar App Shell | bottom tab bar → nav rail → sidebar (promotes by width) | Touch-first / consumer apps, PWAs |
| `threepane` | Three-Pane + Right Agent Panel | left nav + center + persistent right agent-chat / artifacts (or inspector) | AI apps & collaborative tools |
| `bento` | Bento Grid Dashboard | header + asymmetric grid of variable-size tiles | SaaS homepages, dashboards |
| `docs` | Documentation 3-Column | left doc-tree + content + right TOC | Developer docs, knowledge bases |

`sidebar` is the **byte-parity baseline**: it is the frontend forge produced
before layouts existed, so selecting it (or omitting `--layout`) reproduces
the previous output exactly.

### Responsive contract

Every layout implements three breakpoint tiers — **mobile `<600px` · tablet
`600–840px` · desktop `≥840px`** — off a single per-framework source, and the
thresholds match across frameworks: Vue `useBreakpoint`, Svelte
`breakpoints.svelte.ts`, and Flutter `DesignTokens` (`compactWidth` /
`expandedWidth`). Each layout owns its per-tier reflow, e.g. `tabbar` promotes
its bottom tab bar to a nav rail then a full sidebar; `docs` hides its TOC on
tablet and moves its tree into a drawer on mobile.

## Usage

```bash
# CLI flag
forge --project-name shop --backend-language python --features products \
  --frontend vue --layout topnav --yes

# YAML config
#   frontend:
#     framework: vue
#     layout: threepane
forge --config forge.yaml --yes

# Interactive: after choosing a framework you're prompted for a layout
# (only shown when the framework offers more than one).
```

The choice is persisted in the project's `forge.toml` (`[forge.frontend].layout`)
so `forge --update` and harvest round-trips preserve it.

## UI kits

The Vue template renders its screens on one of two component libraries,
chosen with `--ui-kit` (CLI), `frontend.ui_kit` (YAML) or the interactive
prompt ([ADR-011](../architecture-decisions/ADR-011-ui-kit-option-for-the-vue-template.md)):

- `shadcn` *(default)*: shadcn-vue on Radix Vue and Tailwind CSS 4, the kit the
  template has always shipped.
- `nuxt-ui`: [Nuxt UI 4](https://ui.nuxt.com) on Reka UI and Tailwind CSS 4,
  adopted by its own model: its colour mode, a palette fixed per project in
  the brand module `src/custom/theme/`, lucide icons bundled at build time.
  Agent tooling comes with it ([agentic usage](agentic-usage.md#nuxt-ui-projects)).

Svelte and Flutter keep their own kits. Each layout lists the kits its files
render with, and forge refuses a combination it cannot render before writing
anything:

| Layout | `shadcn` | `nuxt-ui` |
|--------|----------|-----------|
| `sidebar` | yes | yes |
| `topnav`, `tabbar`, `threepane`, `bento`, `docs` | yes | not yet |

Under `nuxt-ui` the AI chat (`include_chat`) and the component features built
on shadcn-vue are refused as well; the refusal names the Nuxt UI component to
use instead where there is one. `--color-scheme` takes the template's eight
schemes for both kits and, for `nuxt-ui`, any Tailwind CSS 4.3 palette name.

## Application templates

`frontend.app_template` chooses what a Vue app contains
([ADR-015](../architecture-decisions/ADR-015-frontend-application-templates-layouts-and-menus.md)):

- `crud-app` *(default)*: the sample application — a CRUD slice per backend
  feature, home, profile, settings and sign-in pages, and the layouts above.
- `app-shell` (`ui_kit: nuxt-ui` only): the boilerplate, a configurable shell
  and no page of its own. The project declares its layouts and their menus,
  its routing contracts, its authentication settings and the extras it wants;
  forge renders them, and the project composes its own pages into the router
  factory forge generates. Nothing is written under `src/features/`.

```yaml
frontend:
  framework: vue
  ui_kit: nuxt-ui
  app_template: app-shell
  include_auth: true
  auth: { runtime: forge, session_dialog: builtin }   # runtime: forge | project
  extras: []                       # home, profile, settings, health, confirm, list_helpers,
                                   # forms, capabilities, event_stream, layout_helpers, msw
  routing:
    landing: orders                # the route `/` opens; required
    sign_in: builtin               # builtin, or a project route name
    not_found: builtin
    auth_stuck: builtin
    forbidden: builtin
  layouts:                         # the first is mounted at `/`, the others under `/<name>`
    console:
      shell: header-sidebar
      placeholder: not-built       # a project route taking :item; without it such items are disabled
      menus:
        primary:                   # the sidebar
          - items:
              - { id: overview, label: Overview, icon: i-lucide-house, to: overview }
              - id: sales
                label: Sales
                icon: i-lucide-shopping-cart
                children:
                  - { id: orders, label: Orders, to: orders }
                  - { id: invoices, label: Invoices, badge: invoices }   # no destination: placeholder
          - label: Settings        # a section's eyebrow label
            items:
              - { id: team, label: Team, icon: i-lucide-users }
        account:
          - items:
              - { id: sign-out, label: Log out, icon: i-lucide-log-out, action: sign-out }
        footer:
          - items:
              - { id: support, label: Support }
```

An item has at most one of `to` (a route name), `action` (an id the project
passes to the router factory) and `children` (one level); ids are kebab-case and
unique within a layout; `badge` names a key whose value the project supplies at
runtime with `provideMenuBadges` (account-menu badges included). A route marks the
item it belongs to with `meta.menu: <id>`; without it, the item whose `to` is a
matched route is active. The active item's parent is open, and follows navigation.
A placeholder item opens the placeholder route with `{ item: <id> }` when that
route takes `:item`, without params otherwise.

The routing contracts: `/` always opens `landing` (the `home` extra sits at
`/home`); `landing` may not be a system destination (the sign-in route, built in or
not, the not-found, auth-stuck or forbidden route), which would loop, and the
guards never redirect a visit to the route it is on. A project route named as
`not_found` also answers every unknown address, keeping the address. Every
`meta.requiresRole` along the matched routes must hold, and the deepest route that
sets `meta.requiresAuth` decides whether it needs a sign-in. The app mounts without
waiting for the authentication contract to start; a sign-in that does not answer
in time ends on `auth_stuck`.

What forge writes, and who owns it:

| Path under `apps/<name>/` | Ownership |
|---|---|
| `src/shared/application/` (contracts, the rendered configuration, the router factory, guards, menus, the forge auth adapter) | generated |
| `src/shared/layouts/HeaderSidebarShell.vue` | generated |
| `src/shared/system/` (the built-in sign-in, not-found, auth-stuck and forbidden pages) | generated |
| `src/shared/pages/` (the `home`, `profile` and `settings` extras) | generated |
| `src/app/main.ts`, `src/app/App.vue`, `src/app/auth.ts`, `src/app/router/index.ts` | scaffold |
| the tests beside those modules, `tests/integration/application.test.ts`, `tests/e2e/application.spec.ts` | scaffold |

Generated code never imports project code. The project composes once, in
`src/app/router/index.ts`:

```ts
const router = createApplicationRouter({
  auth,                                   // src/app/auth.ts: forge's runtime or the project's
  routes: { console: consoleRoutes },     // the project's pages, per layout
  publicRoutes: [signInRoute],            // outside every layout
  layouts: { console: ConsoleLayout },    // a wrapper filling the shell's slots
  actions: { 'sign-out': () => auth.signOut() },
  activeLayouts: ['console'],             // default: every layout (choose by host, for example)
})
```

The factory throws when a menu item, a placeholder or a routing contract names a
route the composition does not contain, or when two routes share a name. The
`header-sidebar` shell renders a full-width header (slots `logo`, `header`,
`actions`), a collapsible sidebar with the primary menu, the page with a
`footer` slot, a right-hand `aside` panel that collapses on wide screens and
slides over the page on narrow ones, and the two edge toggles; a wrapper passes
its slots through to it. With `auth.runtime: project`, forge emits no
`useAuth`, no session-timeout composable and no forge auth adapter, and
`src/app/auth.ts` is a starter to implement.

Changing a menu or a route contract is a configuration change: edit the stack
file and the recipe's `config.frontend`, then run `forge --update`.

## Architecture

Two orthogonal concerns:

- **Dispatch** — *which* shell renders. A layout is the entire shell skeleton,
  so it maps to a Copier **template variant** selected by a
  `(framework, layout)` registry, **not** an option/fragment. (Fragments are
  additive injections, and `plan_validator`'s file-overlap check forbids
  several fragments writing the same shell file — a structural dead end for
  whole-shell selection.)
- **Composition** — *how* each shell is built: from reusable **Layer-2** region
  components (`AppHeader`, `SidebarNav`, `BottomTabBar`, `RightPanel`,
  `TocPanel`, `BentoGrid`, …) built from **Layer-1** basics. A layout is a thin
  Layer-3 arrangement of regions; nothing is re-implemented per layout.

The shared, layout-invariant scaffolding (router, auth, API client, the
Layer-1/2 component library, codegen landing paths) lives in the **base**
template (`templates/apps/vue-frontend-template`). A non-default layout is a
**two-stage render**: forge renders the base first, then overlays the layout's
thin variant template (its `MainLayout.vue` + any layout-specific regions). This
was validated byte-identical to a single render. `sidebar` is self-contained
(single render) and needs no overlay.

`frontend_layout` is threaded through `variable_mapper → answers.json →
post_generate`. The base renders the `sidebar` shell's chat-off and per-kit
variants from Copier conditional file names; other layouts gate their chat
with `{% if include_chat %}` and degrade cleanly when chat is disabled.

## Adding a layout

Adding a layout is a **drop-in** — no changes to `generator.py`:

1. Create `forge/templates/layouts/<framework>/<slug>/layout.toml`:
   ```toml
   [layout]
   name = "<slug>"
   framework = "vue"
   display_label = "My Layout"
   template_dir = "layouts/vue/<slug>"   # relative to forge/templates
   base = "apps/vue-frontend-template"    # "" for a self-contained variant
   supported = true
   ui_kits = ["shadcn"]                   # the Vue UI kits its files render with
   ```
2. Add the thin overlay under `template_dir/template/` — at minimum
   `src/shared/layouts/MainLayout.vue.jinja` composing base components, plus any
   new Layer-2 region components. Copy the base's `copier.yml` into the variant
   dir. Use `.vue.jinja` so `{% if include_chat %}` and `{{ app_title }}` render;
   wrap Vue mustaches in `{% raw %}…{% endraw %}`. Preserve the
   `// --- feature nav items ---` / `// --- end feature nav items ---` markers in
   any nav you render so the feature injector can splice generated routes.

The layout is auto-discovered on the next run; `forge --frontend <fw> --layout
<slug>` and the interactive picker pick it up automatically.

**Plugins** register layouts at runtime instead of shipping a manifest:

```python
def register(api):
    api.require_sdk(">=1.6")
    api.add_frontend_layout(
        "vue", "mylayout", "/abs/path/to/template", "My Layout",
        base_template_dir="apps/vue-frontend-template",
        ui_kits=("shadcn",),  # SDK 1.6; the default
    )
```

## See also

- [Layered components (Vue 3)](../../README.md#layered-components-vue-3)
- [Adding a frontend](adding-a-frontend.md)
