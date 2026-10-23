# Forge: a Nuxt UI kit option for the Vue template

Base: f1e7ba714fca490f8d72caa367f9e34697c72930.

Date: 2026-10-22. Status: accepted. The decisions below were confirmed with the
owner on 2026-10-22, who approved ADR-011 and F-01 the same day. F-01 is
merged (#376, `78f67083`) into `cchifor/forge` and Kartra's fork
`kartrainc/forge`, which F-02 and F-03 target from now on. F-02 is built on
`feat/vue-nuxt-ui-kit`, and §"F-02 as built" records where it settled the text
above differently. First consumer:
kartra-ai, which plans its own adoption in its repository.

Requested scope: offer Nuxt UI 4 as a selectable kit of the Vue template,
adopted by its documented model rather than adapted to the theming of the
shadcn-vue kit; keep shadcn-vue the default. Added the same day: repair the
`security.csp` option so it serves a working per-request nonce policy for both
kits, which the Nuxt UI wiring then passes on (§F).

## Context

The Vue template ships shadcn-vue as its only kit, and the ownership model keeps
that kit in every generated tree: `src/shared/ui/` is `generated`, the screens
around it are `scaffold`. A project that wants Nuxt UI today installs it beside
the generated kit and hooks scaffold files to make the two coexist:

- `main.css` defines `--color-primary`, `--color-secondary` and `--color-muted`
  in `@theme inline` for shadcn-vue, and Nuxt UI defines the same utilities from
  its `--ui-*` tokens, so each name needs an owner the generator knows nothing
  about.
- The generated screens (settings, profile, the CRUD pages) keep rendering on
  shadcn-vue and drift from the project's look.
- `.dark` gets two writers: the generated settings store and Nuxt UI's colour
  mode, each with its own storage key.
- Every hook is an edited scaffold file, so every generator update that changes
  the same file proposes it again as a `.forge-merge` sidecar.

Fixing this in forge removes the cause. Once the generator emits the Nuxt UI
wiring, the ownership rules protect it instead of fighting it, the generated
screens are Nuxt UI screens, and the official `nuxt-ui` agent skill and MCP
configuration travel with the generator. The `nuxt-ui` variant carries nothing
of the shadcn-era theming model: the clashes it was built around do not exist
once the shadcn kit is absent.

A strict style policy is the other half. `security.csp` is on by default, yet
the snippet it ships cannot load and no generated server includes it, so no
generated project serves a policy. Under a strict policy a consumer writes for
itself, Nuxt UI's runtime palette element is blocked and every component loses
its colours. Repairing the option gives both kits a working nonce, and the Nuxt
UI wiring passes it on.

## Grounding — forge at the base

Read at the base on 2026-10-22.

| Area | Fact |
| --- | --- |
| The Vue template | `forge/templates/apps/vue-frontend-template/`: a Copier template of 281 files; `copier.yml` questions include `include_auth`, `include_chat`, `include_openapi`, `include_sentry`, `frontend_layout`, `default_color_scheme` (`include_dag_editor` arrives from the variable mapper); `_tasks` runs `scripts/post_generate.py`, which also renders the CRUD pages of every generated feature from `scripts/feature_templates.py` (743 lines: list, create and detail pages importing `@/shared/ui/card`, `button`, `input`, `label`, `textarea`, `skeleton`, `dropdown-menu`). Only `.jinja` files are templated; `.vue` files are copied verbatim, and Copier skips any path part that renders empty. |
| The kit | `src/shared/ui/` with 20 shadcn-vue component directories (1,458 lines) on radix-vue 1.9, `class-variance-authority`, `clsx`, `tailwind-merge`, `@lucide/vue`, `vue-sonner`; `components.json`, `tailwind.config.ts`; the generated README names "Shadcn-Vue (Radix Vue + Tailwind CSS 4)". |
| The screens | about 2,000 lines outside the kit: `MainLayout.vue` (203), `AppSidebar.vue` (193), `SettingsPage.vue` (161, the theme control on the settings store), `HomePage.vue` (145), `AppHeader.vue` (104), `ProfilePage.vue` (97), `LoginPage.vue` (83), `ConfirmDialog`, `ConfirmHost`, `StatusBadge`, `HealthIndicator`, `VerticalSplitter`, `EmptyState`, `AuthStuckPage`, `NotFoundPage`, `AuthLayout`. The `ai_chat` feature (7,906 lines; 20 of its 63 files import a kit module, 12 of them `@/shared/ui`) is emitted only with `include_chat`; with it off only the two generated protocol files land. |
| The post-generate script | `post_generate.py` (568 lines) carries three Vue sources as Python strings: the no-chat `MainLayout.vue` (53 lines, written over the template's when `include_chat` is off under the `sidebar` layout), the no-chat `App.vue` and the no-Keycloak `useAuth.ts`; it writes the app title into `AppSidebar.vue` by string replacement. Chat off under `sidebar` is the first consumer's configuration, so the layout it renders is the Python string, not `MainLayout.vue`. |
| Theming | `settings.store.ts` is the one writer: `.dark` and `.oled` on `<html>`, `--primary-h/s/l` from 20 runtime schemes, `--font-size` for the text-size axis, four `localStorage` keys; `useTheme.ts` wraps it; `main.ts` applies it and listens to `prefers-color-scheme`. `tailwind.config.ts` (`darkMode: 'class'`) is a Tailwind 3 leftover nothing reads, and no CSS declares a class-based `dark` variant, so the `dark:` utilities in seven template files follow the operating system, not the class. |
| Config model | `FrontendConfig` (`forge/config/_frontend.py`): `framework`, `include_auth`, `include_chat`, `include_openapi`, `default_color_scheme` (Vue only), `layout`; `forge/cli/builder.py` maps `frontend.*` of a stack file onto it; `forge/variable_mapper.py` passes the values to Copier in four per-framework context builders; `forge --schema` publishes the option registry only (`frontend.mode`, `frontend.api_target.*`, `frontend.openapi_spec_url`), not these fields. |
| Layout variants | `forge/layout_variants.py` and `forge/templates/layouts/vue/<name>/layout.toml`: `sidebar` is the base template rendered once; `bento`, `docs`, `tabbar`, `threepane`, `topnav` are thin overlays rendered after the base (two-stage render), 25 files in all, 9 of them importing a kit module: `@lucide/vue` in all 9, `@/shared/ui/avatar` and `dropdown-menu` in 2. `supported = false` registers a variant while hiding it from selection. |
| Ownership | `forge/quality/model.py`: under `apps/<app>/`, `src/shared/**` is `generated`; a path containing `custom`, `tests`, `test` or `docs` is `scaffold`; a fragment's source files (`.py`, `.ts`, `.vue` and the like) are `generated` and its other files `scaffold`; everything else emitted is `scaffold`; files not emitted are `user`. A brand module under `src/custom/` is scaffold with no new rule. |
| Component features | Eight component features emit Vue files that import the kit: `data_table` (Layer 1, seven files with its own popover and checkbox), `async_state_block` (1), `checkbox_input`, `switch_input`, `popover_input`, `page_actions` (1), `notifications` (2), `dag_editor`; `status_dot`, `stat_card` and `chatfirst_template` import nothing from it but use its `text-muted-foreground` class. Component features compile to project-scoped fragments gated on `target_frontends`, with the template tree hard-coded as `component_<Name>/all/files/` (`forge/components/_compile.py`); `tests/test_features_ui_primitives.py` asserts the radix import. |
| Frontend fragments | `platform_auth_session_timeout_vue` (the composable under `src/shared/composables/` and `SessionTimeoutModal.vue` under `src/features/auth/components/` since #371) with Svelte and Flutter siblings; its `all/files/` tree is copied verbatim; the only frontend gating is `target_frontends`, applied in `forge/capability_resolver.py`; `tests/test_three_frontend_coverage.py` resolves siblings by stripping `_vue` and requires a sibling or a recorded skip reason for every Vue-shipping fragment. |
| Update | `forge --update` renders a candidate from the recipe and compares provenance: protected files are rewritten, untouched scaffold is updated in place, edited scaffold gets a `.forge-merge` sidecar, and while any sidecar is open only sidecars are written. Generated files that vanish from the new provenance are deleted; vanished scaffold is left on disk; new output colliding with an existing unrecorded file aborts the update. |
| The rendered-quality lane | `scripts/check_generated_quality.py --language vue` renders auth, chat and OpenAPI off, then checks the architecture, locks and audits the dependencies, and runs Vitest unit and integration and Playwright through `forge/quality/runner.py`; it does not run the app's lint or type-check. A fresh render has no git base, so the gate is total: more than 80 % of every executable line, all three suite reports present, no source file absent from them. The generated `.env` sets `VITE_AUTH_DISABLED` from `include_auth`; the runner does not override it. |
| Tests touching the kit | `tests/integration/button.test.ts` and `primitives.test.ts` mount the shadcn kit by relative import; `splitter.test.ts` and `status-confirmation.test.ts` mount `VerticalSplitter`, `StatusBadge` and `ConfirmHost`; `settings-page.test.ts` asserts the settings store. `tests/test_golden_snapshots.py` hashes `.forge/quality.json` (the recipe serialises every `FrontendConfig` field) and tracks the size of `forge.toml`. |
| Common files | `forge/templates/_common/`: the `forge-platform` skill copied to `.agents/skills/` and `.claude/skills/`, `claude-settings.json`, `AGENTS.md`, `CLAUDE.md`, the CI workflows, the browser-coverage scripts (`forge/common_files.py`, copy-if-absent); `LICENSE.MIT` already ships for vendored MIT material. |
| Content security | `security.csp` is on by default and only emits `deploy/infra/nginx-csp.conf` from the `security_csp` fragment. Nothing includes the snippet; it references `$csp_nonce`, which nothing defines, so nginx refuses to start with it; the `vite-plugin-csp-guard` it names is not a dependency; and it enforces `require-trusted-types-for 'script'`. The frontend's `nginx.conf`, rendered from `forge/templates/deploy/nginx.conf.j2` as scaffold and supplied at runtime by a compose bind-mount or by the Helm chart's ConfigMap, whose default server block is in `deploy/helm/templates/frontend.yaml`, sends no headers. `index.html` puts an inline `style` attribute on `<body>` and links Google Fonts. The first consumer recorded the snippet as a generator defect and serves a policy of its own. |
| Style policies | Without `'unsafe-inline'`, a style policy blocks `<style>` elements and `style` attributes parsed from markup or set with `setAttribute`. CSSOM writes are exempt: Vue's `:style` bindings, the static `style` of compiled templates, `el.style.*`, `setProperty`, `cssText`. The first consumer measured its production build under such a policy with no style violation; its one report is a `script-src` one from zod's guarded `Function("")` capability probe. Vue inserts large static template blocks as markup, so `style` attributes inside them are blocked. |
| Records and release | decisions in `docs/architecture-decisions/ADR-NNN-<slug>.md` (last: ADR-010); `CHANGELOG.md` in Keep-a-Changelog form with an Unreleased section that still says "targeting 1.2.0" although `v1.2.0` is tagged and `__version__` is 1.2.0; a release is a tag; consumers may pin commits rather than tags. |

## Nuxt UI 4.12.0 in Vue mode

Verified on 2026-10-22 against ui.nuxt.com and the `v4` branch of `nuxt/ui`.
These facts shape the approach.

- **Wiring.** The entry is `@nuxt/ui/vue-plugin`, a virtual module;
  `@nuxt/ui/vue` does not exist. `tailwindcss ^4` is the one required peer. The
  Vite plugin bundles `@tailwindcss/vite` itself and registers its own
  `unplugin-auto-import` and `unplugin-vue-components`, throwing on a second
  instance. `auto-imports.d.ts` and `components.d.ts` are written only when Vite
  runs, so a type-check before the first build fails.
- **Colour mode.** The plugin registers VueUse's `useDark`, which writes `.dark`
  on `<html>` from the key `vueuse-color-scheme` (`auto`, `light`, `dark`).
  `index.css` declares `@custom-variant dark (&:where(.dark, .dark *))`.
  `UColorModeSelect`, `UColorModeSwitch` and `UColorModeButton` read the same
  key through `useColorMode` from `@vueuse/core`. `colorMode: false` removes only
  that plugin and makes those components inert.
- **Colours.** `ui.colors` are Tailwind palette names. In Vue mode
  `useAppConfig()` is a module-level reactive object and the colours plugin
  recomputes its palette from it, so runtime switching is one assignment. The
  palette is a `<style id="nuxt-ui-colors">` element injected at runtime, with
  no nonce hook.
- **Icons.** `UIcon` wraps `@iconify/vue`. Installing `@iconify-json/lucide`
  bundles Nuxt UI's own defaults and `ui.icons` only; the app's own `i-lucide-*`
  need `icon.clientBundle` (`scan`, which reads `.vue` but not `.ts` without
  `globInclude`, or an explicit list). Dynamic names cannot be bundled.
- **Components.** Everything this plan names exists: `USplitter` since 4.11,
  `UEmpty` since 4.1, the Dashboard family since the Pro merge.
  `UDashboardGroup` defaults to cookie storage, a no-op stub in Vue, so it needs
  `storage="local"`. `UTable` nests `@tanstack/vue-table` v8. Fonts are not
  auto-loaded in Vue mode.
- **Content security.** The docs give no guidance. Reka UI positions floating
  content through the CSSOM, so a client-rendered app sees no violation; the
  author of issue 2732 retracted its premise after instrumenting it, and only
  server rendering emits blocked attributes. Reka does create `<style>`
  elements: in the Select, Combobox and ScrollArea viewports to hide
  scrollbars, and while a Splitter handle drags to set the cursor. Each takes
  the nonce `UApp` passes to Reka's `ConfigProvider`. The palette element goes
  through unhead's `useHead` without a nonce. Unhead's client renderer copies
  each tag's props onto the element before inserting it and offers a
  `tags:resolve` hook, and Nuxt UI's Vue head plugin reuses a head instance the
  app installed first, so the app can stamp the nonce on it. That is read from
  source and proven in F-02.
- **Vite.** `html.cspNonce` sets a placeholder that Vite writes on its script,
  style and stylesheet tags, together with a `<meta property="csp-nonce">` tag
  the app reads at runtime. The server replaces the placeholder per response.
- **Agent tooling.** The skill is `skills/nuxt-ui/` on the `v4` branch
  (`name: nuxt-ui`, MIT through the repository licence). The MCP server is HTTP
  at `https://ui.nuxt.com/mcp`. The docs give the Claude Code CLI command and a
  Cursor file, no `.mcp.json` or Codex snippet.

## Decisions (confirmed with user)

1. **Default:** `shadcn` stays the default; its rendered output changes only
   through the `security.csp` repair of F-02.
2. **Mechanism:** kit-specific files side by side in the base template under
   Copier conditional names (§C); no overlay.
3. **Theming by the book:** Nuxt UI's colour mode is the one writer of `.dark`,
   the palette is fixed per project, and the `nuxt-ui` kit carries no runtime
   scheme picker, no `.oled` variant and no text-size axis (§E).
4. **Auto-import owner:** Nuxt UI, by necessity, since its plugin refuses a
   second `unplugin-auto-import` instance.
5. **Content security:** `security.csp` is repaired in F-02 to serve a
   per-request nonce policy for both kits, and the `nuxt-ui` app runs under it
   with no style violation (§F).
6. **Version:** `@nuxt/ui` with a caret on the major (`^4.12.0`), held as a
   forge constant like the AG-UI pins. The rendered lane already freezes npm
   resolution to a snapshot date and a consumer's lockfile pins exactly. The
   vendored skill follows the minor.
7. **Agent tooling:** forge vendors the official skill and writes the MCP files
   when the kit is `nuxt-ui` (§G).
8. **Pull requests:** three (see Phasing); the `security.csp` repair rides in
   F-02.
9. **Fonts:** the template keeps its Google Fonts link in both kits, and the
   policy names the two Google hosts.

## Approach

### A. The option

`frontend.ui_kit` with the values `shadcn` (default) and `nuxt-ui`. It lands in
`FrontendConfig.ui_kit`, the stack file (`frontend: { ui_kit: nuxt-ui }`), the
CLI (`--ui-kit`), the interactive prompt, the Copier variable `ui_kit`,
`forge.toml` (`[forge.frontend] ui_kit`, absent meaning `shadcn` in older
manifests), `.copier-answers.yml` and the recipe's `config.frontend`. It follows
the `layout` precedent exactly, so not `forge --schema`, which publishes
options rather than these fields.

Vue only: Svelte and Flutter keep their kits, and ADR-011 records the asymmetry
the way ADR-007 records the three templates. `nuxt-ui` is registered hidden in
F-01 and made selectable in F-02, so no merge commit accepts a kit it cannot
render.

### B. Plan-time validation

A combination forge cannot render fails before anything is written.

- **Kit × layout:** `layout.toml` gains `ui_kits`. `sidebar` lists both kits;
  the overlays list `shadcn` until ported.
- **Kit × component feature:** `[feature.component]` gains `ui_kits`, defaulting
  to `["shadcn"]`, and `replaced_by`, which names the component a kit offers
  instead (`UCheckbox`, `USwitch`, `UPopover`, `UTable`, `UFieldGroup`,
  `UEmpty and USkeleton`). A feature lists `nuxt-ui` when it ships a
  `files-nuxt-ui/` tree or its files are kit-free: in F-01 that is EntityList
  and the Console template. StatCard, StatusDot and ChatFirst import nothing
  from the kit but use its `text-muted-foreground` class, which Nuxt UI does not
  define, so they declare `shadcn` only. The refusal propagates through
  component children: Console is refused for `nuxt-ui` through StatCard.
- **Kit × fragment:** `Fragment` gains `ui_kits` (empty meaning any kit).
  `tests/test_ui_kit_coverage.py` (F-01) fails any Vue-shipping fragment or
  component feature that renders with `nuxt-ui`, by declaration or by declaring
  nothing, yet imports `@/shared/ui`, `radix-vue`, `@lucide/vue` or `vue-sonner`
  or uses one of the shadcn colour classes. In F-02 the applier takes
  `files-<kit>/` over `files/` when present, with its first user:
  `platform_auth_session_timeout_vue` ships both trees under one name, so
  `tests/test_three_frontend_coverage.py` is unchanged.
- **Kit × chat:** `nuxt-ui` with `include_chat` is refused until phase 2.
- **Kit × colour:** `default_color_scheme` keeps its eight values for both kits.
  Under `nuxt-ui` they map to Tailwind palette names (`deepPurple` to `violet`,
  the rest by name), and any Tailwind palette name is accepted as well. F-01
  validates this on the configuration; F-02 widens the `--color-scheme` choices
  and `copier.yml` when `nuxt-ui` becomes selectable.

### C. Rendering mechanism

Kit-specific files sit beside each other in the base template under Copier's
conditional file names, and `src/shared/ui/` sits under a conditional directory
name, so a reader sees both variants of a screen in one place. The shared
plumbing (router, auth, API client, mocks, tests other than the kit's) is
untouched. Copier skips any path part that renders empty and copies `.vue` files
verbatim, so no Vue file needs Jinja.

An overlay was rejected: it cannot delete the base's kit directory, so it would
need the base conditional anyway, and a third render stage would have to
interleave with the layout overlays.

In F-01 the three Vue sources held as Python strings in `post_generate.py` move
into conditionally named files, with the golden gate proving the shadcn output
byte-identical. F-02 then adds their `nuxt-ui` siblings instead of growing the
script.

### D. What `nuxt-ui` renders

| Piece | Rendered |
| --- | --- |
| dependencies | `@nuxt/ui` at the constant's range, `tailwindcss` (already present), `@iconify-json/lucide`. Dropped: `radix-vue`, `class-variance-authority`, `clsx`, `tailwind-merge`, `@lucide/vue`, `sonner`, `vue-sonner`, `@tailwindcss/vite` (the Nuxt UI plugin bundles it), `unplugin-auto-import` (Nuxt UI registers its own), `@tanstack/vue-table` (nothing outside the kit uses it; `UTable` nests its own), `components.json`, `tailwind.config.ts`. `src/shared/ui/` is not emitted |
| wiring (scaffold) | `vite.config.ts`: `ui()` after `vue()` and no `tailwindcss()` or `AutoImport()` call; the `ui` and `theme` options imported from the brand module; `autoImport` carrying the template's import list, directories and `dts: 'src/auto-imports.d.ts'`; `icon.clientBundle` with `scan` (its `globInclude` extended to `.ts`) plus an explicit list for icons named in data; `experimental.componentDetection: true`; `colorMode` and `router` at their defaults; `html.cspNonce` set to the placeholder when `security.csp` is on; the chunk group `vendor-nuxt-ui` in place of `vendor-radix`. `main.ts`: `import ui from '@nuxt/ui/vue-plugin'`, `app.use(ui)` after the router and, when `security.csp` is on, after an unhead head instance whose `tags:resolve` hook stamps the nonce from the `csp-nonce` meta tag on style tags; no theme application and no `prefers-color-scheme` listener. `App.vue`: `<UApp :toaster :nonce>` around the router view, no sonner; its no-chat variant a conditionally named sibling. `index.html`: `class="isolate"` on the mount node, `bg-default text-default` on the body, no inline `style` attribute (the font family moves to `--font-sans`). `src/app/styles/main.css`: the two imports, the brand tokens import, the button-cursor base rule. `tsconfig.app.json` and `tsconfig.node.json`: the `#build/ui` paths; `components.d.ts` in the include list and in `.gitignore`. `package.json`: `build` runs Vite before the type-check |
| the brand module (scaffold, written once) | `src/custom/theme/tokens.css`: `@theme static` with the fonts and `--ui-radius`; the `--ui-*` surface overrides under `:root` and `.dark`. `src/custom/theme/nuxt-ui.config.ts`: `colors` (`primary` from `default_color_scheme`, `neutral`), `icons`, `theme.colors` trimmed with `error` kept for forms, `theme.defaultVariants`, the per-component themes, empty. A project's edits are never overwritten; a later change of the starter arrives as a sidecar like any scaffold file |
| screens | `MainLayout` on `UDashboardGroup` (`storage="local"`), `UDashboardSidebar` and `UDashboardNavbar`, the navigation as `UNavigationMenu` built from the generated features, its no-chat variant a conditionally named sibling; `AuthLayout` and `LoginPage` on `UCard` and `UButton` with the Keycloak flow unchanged; `AuthStuckPage`, `NotFoundPage` and `EmptyState` on `UEmpty`; `HomePage` as `UPageCard` links that follow the generated features (the rule of #372 kept); `ProfilePage`; `SettingsPage` with `UColorModeSelect` as the theme control; `ConfirmDialog` and `ConfirmHost` on `UModal` and `useOverlay`; `StatusBadge` and `HealthIndicator` on `UBadge`; `VerticalSplitter` on `USplitter`; the CRUD pages of `feature_templates.py` on `UTable` or `UCard` lists, `UForm` with `UFormField`s, `UDropdownMenu` row actions and `USkeleton`; the session-timeout fragment's dialog on `UModal` from its `files-nuxt-ui/` tree |
| icons | `UIcon` with `i-lucide-*` strings, bundled through `icon.clientBundle`; nothing fetches from the Iconify API |
| README | "UI: Nuxt UI 4 (Reka UI + Tailwind CSS 4)", "Icons: Iconify, lucide bundled" |

### E. Theming

By the book, with nothing of the shadcn-era model carried over.

- **Mode.** Nuxt UI's colour mode stays on. VueUse's `useDark` is the one writer
  of `.dark` and `vueuse-color-scheme` the one key. Code that needs the mode
  calls `useColorMode` from `@vueuse/core`; the settings page uses
  `UColorModeSelect`.
- **Palette.** Fixed per project in the brand module, `primary` from
  `default_color_scheme`. Runtime switching would also be by the book, through
  the reactive app config. It was left out partly because only a nonce could
  admit a changing palette element; with §F's nonce that reason is gone, so a
  picker is a product choice a project or a later forge change can make.
- **Not emitted under `nuxt-ui`:** `settings.store.ts`, `useTheme.ts`, the
  theme application in `main.ts`, the `.oled` variant, the twenty runtime
  schemes, the text-size axis, `tailwind.config.ts`.
- **Consequences.** The class-based `dark` variant Nuxt UI installs makes every
  `dark:` utility follow `.dark`, which is the intended behaviour. A project
  wanting a pure-black dark surface overrides `--ui-bg` under `.dark` in its own
  tokens. A project switching kits moves its stored mode from `theme` to
  `vueuse-color-scheme`, `system` becoming `auto` (UPGRADING).

### F. Content security

Repaired in F-02 for both kits; the `nuxt-ui` wiring of §D passes the nonce on.

- **Serving.** With `security.csp` on, `nginx.conf.j2` and the Helm chart's
  default server block send the policy themselves. Its directives are the
  snippet's, with `$request_id` (16 random bytes per request, as hex) as the
  nonce in `script-src` and `style-src`, and the two Google Fonts hosts added to
  `style-src` and `font-src`. A `sub_filter` writes the same value over the
  placeholder in `index.html`, which is served `no-store`; hashed assets stay
  cacheable. The `security_csp` fragment stops shipping the standalone snippet.
  With the option off, nothing is emitted.
- **Trusted Types.** The snippet's `require-trusted-types-for 'script'` stays
  only if both lanes pass with it enforced. Vue 3.5 routes its own HTML writes,
  `v-html` and Iconify's SVG bodies included, through a Trusted Types policy of
  its own; unhead's raw `innerHTML` for the palette element is unproven. If a
  lane fails, F-02 drops the directive from the enforced header, ADR-011
  records why, and Trusted Types becomes an open point.
- **Building.** `vite.config.ts` sets `html.cspNonce` to the placeholder, so
  Vite stamps its own tags and emits the `csp-nonce` meta tag. `index.html`
  carries no inline `style` attribute in either kit.
- **The app.** Under `nuxt-ui`, `main.ts` installs the unhead hook that stamps
  the nonce on the palette element, and `App.vue` hands it to `UApp`, which
  passes it to Reka's `ConfigProvider`. Under `shadcn`, `App.vue` passes it to
  radix-vue's `ConfigProvider`, which accepts it in 1.9.17.
- **Exempt.** Positioning and every Vue style binding go through the CSSOM.
  Generated templates keep `style` attributes out of large static blocks, which
  Vue may insert as markup.
- **The check.** Both Vue lanes serve the built app under the policy exactly as
  nginx sends it, the placeholder replaced, and fail on any violation of a
  style, connect or Trusted Types directive. Script-source reports are out of
  scope: zod's guarded `Function("")` capability probe reports on every page and
  falls back without effect.
- **Fallback.** If the unhead hook does not stamp the palette element, the
  brand module declares the palette variables statically, the check admits that
  one element by its id, and forge asks Nuxt UI to pass `UApp`'s nonce to it.

### G. Agent tooling

`forge/templates/_common/skills/nuxt-ui/` holds the official skill vendored from
`skills/nuxt-ui/` on the `v4` branch at a recorded commit, with the MIT licence
beside it. When the kit is `nuxt-ui`, `forge/common_files.py` copies it to
`.agents/skills/nuxt-ui` and `.claude/skills/nuxt-ui`, and copies `.mcp.json`
and `.codex/config.toml` if absent, each naming the `nuxt-ui` HTTP server at
`https://ui.nuxt.com/mcp` in the shape the Claude Code and Codex documentation
gives. The vendored commit follows the `@nuxt/ui` minor.

### H. Phases

Phase 1 covers the first consumer's configuration: the `sidebar` layout,
`include_auth` on, `include_chat` off, the CRUD features, the session-timeout
fragment. Phase 2, when a consumer needs it: the `ai_chat` UI (20 of 63 files
import a kit module), the layout overlays (9 files), the Nuxt UI variants of the
eight component features (`data_table` the largest).

## Critical files

- **Created:** `docs/architecture-decisions/ADR-011-ui-kit-option-for-the-vue-template.md`,
  `forge/ui_kits.py`, `tests/test_ui_kits.py`, `tests/test_vue_ui_kit.py`,
  `tests/test_ui_kit_coverage.py`,
  `tests/test_frontend_csp.py`, the `nuxt-ui` variants of the screens, pages
  and suites in the Vue template under conditional names, the conditional
  files that replace the post-generate sources, the session-timeout fragment's
  `files-nuxt-ui/` tree,
  `forge/templates/_common/skills/nuxt-ui/`, `forge/templates/_common/mcp.json`,
  `forge/templates/_common/codex-config.toml`.
- **Changed:** `forge/config/_frontend.py`, `forge/cli/builder.py`,
  `forge/cli/parser.py`, `forge/cli/interactive.py`, `forge/variable_mapper.py`,
  `forge/sync/manifest.py`, `forge/layout_variants.py` and every `layout.toml`,
  `forge/fragments/_spec.py`, `forge/capability_resolver.py`,
  `forge/components/_compile.py`, `_spec.py` and `_registry.py`,
  `forge/feature_manifest.py` and the thirteen component `feature.toml` files,
  `forge/generator.py`, `forge/api.py`, `forge/common_files.py`,
  `forge/quality/runner.py`, `scripts/check_generated_quality.py`,
  `forge/templates/deploy/nginx.conf.j2`, `forge/docker_manager.py`, the Helm
  chart's `templates/frontend.yaml`, `forge/features/security/options.py` and
  `fragments.py`, `.github/workflows/generated-quality.yml`,
  `tests/golden/snapshots/*`; in the Vue template `copier.yml`,
  `package.json.jinja`, `vite.config.ts.jinja`, `index.html.jinja`,
  `main.ts.jinja`, `App.vue`, `tsconfig.app.json`, `tsconfig.node.json`,
  `.gitignore`, `scripts/post_generate.py`, `scripts/feature_templates.py`;
  `tests/test_vue_page_suites.py`, `tests/test_features_ui_primitives.py`,
  `tests/test_features_auth_session_timeout_vue.py`,
  `tests/test_frontend_layouts_render.py`; the guides,
  `docs/reference/cli.md`, `docs/plans/README.md`,
  `docs/architecture-decisions/README.md`, `UPGRADING.md`, `CHANGELOG.md`.
- **Removed:** the `security_csp` fragment's `deploy/infra/nginx-csp.conf`.
- **Not changed:** `forge/quality/model.py`; the brand module is scaffold by the
  existing `custom` rule.

## Phasing — three pull requests

F-01 is Python plumbing proven by the golden gate, reviewed apart from several
thousand lines of Vue. F-02 is the rendering together with the only proof that
it works, the rendered lane. The `security.csp` repair rides in F-02 too: the
lanes are its only proof, the `nuxt-ui` wiring depends on it, and it changes
default-kit output, which would break F-01's byte-identical proof. F-03 is
reviewed by reading. Docs ride with the behaviour they describe. A single pull
request was rejected: one review across plumbing, Vue, a CI lane and docs, with
a red lane blocking all of it.

| PR | Content | Proof | Depends on |
| --- | --- | --- | --- |
| F-01 | ADR-011 and this plan (its `Base` refreshed if `main` has moved); `forge/ui_kits.py` and the option end to end with `nuxt-ui` unselectable; the `ui_kits` declarations of layouts, component features (with `replaced_by`) and fragments, and every refusal of §B; `tests/test_ui_kits.py` and `tests/test_ui_kit_coverage.py`; the three post-generate sources moved into conditional files, with a render test for the chat-off and auth-off variants; the UPGRADING note and changelog entry; the plan index row | `make check`; the golden gate: snapshots change only for the recipe's new field and the generated post-generate script; the seven chat and auth combinations no preset covers compared byte for byte before and after | the owner's acceptance of this plan |
| F-02 | the `security.csp` repair of §F for both kits, with `tests/test_frontend_csp.py`; the `nuxt-ui` rendering of §D and §E, phase 1, `nuxt-ui` made selectable; the `files-<kit>/` applier selection and the fragment's `files-nuxt-ui/` tree, with the coverage lint extended to it; `ui_kits` on `add_frontend_layout` (plugin SDK 1.6); the `--color-scheme` and `copier.yml` choices widened for `nuxt-ui`; the brand module; `tests/test_vue_ui_kit.py` and the parameterised and variant suites; the `vue-nuxt-ui` lane; the content-security check in both Vue lanes and the icon check; `UPGRADING.md`, the changelog's `### Fixed` entry and the README text | both Vue lanes green under forge's gates, the content-security check included; the golden snapshots regenerated deliberately for the repair | F-01 |
| F-03 | the vendored skill and the MCP files; the guides and the CLI reference; the merge commit consumers pin | the tooling assertions of `tests/test_vue_ui_kit.py` | F-02 |

## Verification

- **Golden gate:** in F-01 the rendered application is byte-identical under
  the default kit. Two snapshots change, each in two entries: the recipe gains
  `ui_kit`, and the generated `scripts/post_generate.py` loses its three
  strings. The Copier-answers and `forge.toml` sizes, which the gate does not
  compare, keep `main`'s values. No preset covers chat off or auth off, so F-01
  compared seven renders (sidebar with chat and auth on and off, and three
  overlays) byte for byte before and after, and
  `tests/test_frontend_layouts_render.py` now checks both variants. In F-02
  the snapshots are regenerated again, on purpose, for the `security.csp`
  repair: `nginx.conf`, `index.html`,
  `vite.config.ts`, `App.vue` and the Helm chart change for every project with
  the option on.
- **Policy rendering** (`tests/test_frontend_csp.py`): with `security.csp` on,
  the rendered `nginx.conf` and the Helm chart's default server block send the
  policy with the nonce, the `sub_filter` and `no-store` on `index.html`;
  `vite.config.ts` sets the placeholder; `index.html` has no inline `style`;
  the standalone snippet is not emitted. With the option off, none of this is
  emitted.
- **Page suites:** `tests/test_vue_page_suites.py` is parameterised over both
  kits: every generated CRUD feature gets its page contract, the home page links
  only to generated routes, the profile suite follows the auth variant, updates
  keep edited tests. Under `nuxt-ui`, `button.test.ts` and `primitives.test.ts`
  are not emitted (they test the shadcn kit, which Nuxt UI tests upstream), and
  `splitter.test.ts`, `status-confirmation.test.ts` and `settings-page.test.ts`
  get kit variants.
- **Rendered `nuxt-ui` app** (`tests/test_vue_ui_kit.py`): depends on
  `@nuxt/ui` at the constant's range; has no `src/shared/ui`, no `radix-vue`, no
  `class-variance-authority`, no `@lucide/vue`, no `tailwindcss()` or
  `AutoImport()` call anywhere; imports `@nuxt/ui/vue-plugin`; wraps the router
  view in `UApp`; imports the brand module from the wiring; passes the nonce to
  `UApp` and installs the stamping hook before `app.use(ui)`; lists
  `components.d.ts` in the include and the ignore; carries the mirrored skill
  and the two MCP files; bundles lucide.
- **Refusals:** `tests/test_ui_kits.py` (F-01) covers every refusal of §B with
  `nuxt-ui` made selectable by a fixture, the registry, the CLI, stack-file and
  prompt paths, and what the manifest, recipe and Copier context record.
  `tests/test_ui_kit_coverage.py` (F-01) checks the declarations against the
  files. `tests/test_features_ui_primitives.py` and the fragment tests cover the
  dialog variant in F-02; `tests/test_three_frontend_coverage.py` is unchanged.
- **Rendered lane:** `scripts/check_generated_quality.py` gains `--ui-kit` and
  `--auth`, and `generated-quality.yml` a `vue-nuxt-ui` matrix entry (sidebar,
  auth on, chat off, two CRUD features). The runner sets
  `VITE_AUTH_DISABLED=true` for the frontend suites when auth is on, which Vite
  lets win over `.env`. The gate is the existing total one: more than 80 % of
  every executable line, with unit, integration and e2e reports present. The
  content-security check of §F runs in both Vue lanes, the existing `vue` lane
  included, and a no-icon-fetch check in the `nuxt-ui` lane.

## Records, docs and release

- `docs/architecture-decisions/ADR-011-ui-kit-option-for-the-vue-template.md`:
  the option, the default, the Vue-only scope, the support matrices and the
  `ui_kits` declarations, the conditional-file mechanism, the theming decision,
  the `security.csp` repair it depends on, the phase split, the pull-request
  split.
- `docs/guides/adding-a-frontend.md`, `docs/guides/frontend-layouts.md` (the kit
  × layout matrix), `docs/guides/agentic-usage.md` (the vendored skill and the
  MCP files), `docs/reference/cli.md` (`--ui-kit`).
- `UPGRADING.md`: the option and the plugin declarations (F-01, as the review
  checklist asks for a public-contract change); switching an existing project
  and the `security.csp` repair (F-02, §Consumer adoption).
- `CHANGELOG.md`: an entry under Unreleased with each pull request, as recent
  ones do. `CONTRIBUTING.md` says instead that the changelog is updated on
  release, not per pull request; the maintainer decides which rule holds, and
  the entries are dropped if it is the latter. The Unreleased heading still
  says "targeting 1.2.0" although `v1.2.0` was tagged in June with part of its
  entries; sorting that out is a release task for the maintainer, not part of
  this plan.
- `docs/plans/README.md`: a row for this plan, linked to the guides once the work
  completes.
- A tag (`v1.3.0`) after the first consumer validates the merge commit it pins;
  the pin does not wait for the tag.

## Consumer adoption

A project switches by setting `ui_kit` to `nuxt-ui` under `config.frontend` in
`.forge/quality.json` and running `forge --update`. The update renders from that
recipe and never reads the stack file, so a project that keeps one changes it
too. UPGRADING describes what follows:

- Generated files are rewritten. The kit directory, the settings store and
  `useTheme.ts` vanish from the provenance and are deleted.
- Untouched scaffold is updated in place, the generated screens and CRUD pages
  included. Edited scaffold receives sidecars, typically `package.json`,
  `vite.config.ts`, `index.html`, `main.ts`, `App.vue`, `main.css` and the
  tsconfig files.
- Scaffold the new kit no longer emits stays on disk and is deleted by hand:
  `components.json`, `tailwind.config.ts`, `tests/integration/button.test.ts`
  and `tests/integration/primitives.test.ts`. The two suites import the deleted
  kit and fail until removed.
- The new agent-tooling files must not already exist unrecorded, or the update
  aborts on the collision: `.mcp.json`, `.codex/config.toml`,
  `.agents/skills/nuxt-ui/`, `.claude/skills/nuxt-ui/`.
- The stored colour mode moves from `theme` to `vueuse-color-scheme`.

The `security.csp` repair reaches every project on update, whichever kit it
uses:

- With the option on, the default, the frontend starts serving an enforced
  policy. An untouched `nginx.conf` is updated in place; an edited one receives
  a sidecar. `deploy/infra/nginx-csp.conf` is scaffold, since `.conf` is not a
  source suffix, so the update leaves it on disk; UPGRADING says to delete it.
- Project code that inserts `<style>` elements, or `style` attributes as
  markup, is blocked from then on. UPGRADING lists what to look for and how to
  turn the option off.
- A project that already serves its own policy keeps it and adopts the nonce
  by merging the sidecar.

The first consumer's adoption, including its sidecar resolutions, is planned in
kartra-ai (`docs/plans/2026-10-22-forge-nuxt-ui-kit.md`).

## F-02 as built

Both Vue lanes pass locally against the 2026-10-18 registry snapshot:

| Lane | Coverage (gate > 80 %) | Suites | Content security |
| --- | --- | --- | --- |
| `vue` (shadcn, auth off, `items`) | 1114 / 1236 lines, 90.1 % | 162 unit, 16 integration, 2 e2e | no report |
| `vue-nuxt-ui` (auth on, `items`, `orders`) | 1024 / 1154 lines, 88.7 % | 159 unit, 15 integration, 3 e2e | the two admitted elements only |

Where the build settled the text above differently:

- **Trusted Types is not enforced** (§F's fallback). In Vue mode, Nuxt UI
  4.12's stub reports the app as hydrating, so its colour plugin writes a
  temporary palette copy through `innerHTML`; Trusted Types blocks the write
  and the app does not start. No option turns the copy off. ADR-011 records it.
- **Two upstream elements are admitted.** That palette copy and VueUse's
  colour-mode transition guard insert `<style>` elements without the nonce;
  the browser blocks both without visible effect, since the palette applies
  from the head entry that carries the nonce. `'report-sample'` in
  `script-src` and `style-src` lets the check name them by their first
  characters; any other report fails the lane.
- **More of the app takes the nonce.** vaul-vue, behind `UDrawer` and the
  dashboard's mobile menu, reads it from the `content` attribute of a
  `csp-nonce` meta tag, so the `nuxt-ui` `index.html` carries a second one
  beside Vite's. zod probes for `eval` to compile its parsers; `src/app/providers/csp.ts`,
  imported first by `main.ts` in both kits, sets `z.config({ jitless: true })`.
- **The app's nonce wiring is unconditional.** `csp.ts`, the head hook
  (`src/app/providers/head.ts`) and the `UApp` and `ConfigProvider` props read
  the meta tag and do nothing without it. Only `html.cspNonce`, the vaul meta
  tag and the files of the check follow whether the policy is served.
- **The policy covers the Vue template without the AI chat.** Svelte, Flutter
  and plugin frontends are served without it, since no lane proves them under
  it, and so are projects with the chat: its MCP panels frame remote origins
  that the policy would block. The
  `security_csp` fragment is removed: the option enables no fragment, like
  `auth.api_keys`, and is read by the renderers through `forge/frontend_csp.py`,
  which also admits an external API's origin in `connect-src`.
  Strict-Transport-Security is not sent; it is the TLS terminator's.
- **The check reruns the e2e suites.** `scripts/serve-csp.mjs` serves `dist/`
  with the policy read from `nginx.conf`, a fresh nonce per response and a
  `report-uri`; `playwright.csp.config.ts` points the suites at it. Both are
  common files copied when the policy is on. The e2e specs collect coverage only
  when `FORGE_BROWSER_COVERAGE` is set, and `forge/quality/csp.py` judges the
  reports. It passes only when Playwright exits cleanly with no global error,
  and admits the two elements above only in a `nuxt-ui` project and only by
  their exact sample; the palette copy's sample is also the real palette's, so
  the Nuxt UI e2e spec asserts that the palette applies. The no-icon-fetch
  check is the same run: a request to the Iconify API is a `connect-src`
  violation.
- **`ConfirmHost` stays** instead of `useOverlay`: it and `useConfirm` are
  kit-free; only `ConfirmDialog` has a `UModal` variant. **`VerticalSplitter`
  is not emitted** under `nuxt-ui`, nor its suite: no screen uses it.
  `usePageHeader` takes an icon name besides a component under `nuxt-ui` only.
- **`files-<kit>/` replaces paths.** Its files replace the same paths of
  `files/` and may add their own; the update preview follows the same rule.
- **Entry points.** The prompt asks for the kit before chat and skips chat for
  `nuxt-ui`. `--color-scheme` offers every kit's schemes, and Svelte and
  Flutter now refuse anything outside their eight at validation. `generate()`
  runs the full frontend check, not only the kit name.
- **The lane.** The workflow matrix gains a `kit` axis with one `include`
  entry, since an entry that only adds a key merges into the existing job.
  `scripts/check_generated_quality.py` takes `--ui-kit`, `--auth` and
  `--features`. The runner sets `VITE_AUTH_DISABLED=true` for the browser
  suites only; unit and integration suites keep the project's setting and so
  exercise the real gateway flow.
- **Smaller points.** The shadcn `index.html` loses its inline font style to
  `main.css`. The `nuxt-ui` `build` script runs Vite before the type-check,
  which reads the declarations Vite writes.

## Open points

- Phase 2 scope and timing: the `ai_chat` UI, the layout overlays, the component
  features.
- Self-hosting the font, which would let the policy drop the Google hosts.
- Trusted Types, and the two admitted elements: ask Nuxt UI to skip the
  temporary palette copy in Vue mode or give it `UApp`'s nonce, and to let
  VueUse's transition guard take one.
- A content-security policy for Svelte and Flutter, with a lane that proves it.
- The AI chat under the policy: a `frame-src` for the MCP panels' origins,
  and a lane that runs the chat.
- The reverse-sync harvester maps project files to a fragment's `files/` tree
  and does not know `files-<kit>/` yet.
