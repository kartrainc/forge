# ADR-011: A UI kit option for the Vue template

- Status: Accepted
- Author: forge team
- Date: 2026-10-22
- Scope: `forge/ui_kits.py`, `FrontendConfig.ui_kit`, `forge/layout_variants.py`
  and the Vue `layout.toml` files, `[feature.component]` in component
  manifests, `Fragment.ui_kits` and `files-<kit>/` trees,
  `forge/capability_resolver.py`, `forge/templates/apps/vue-frontend-template/`,
  `forge/frontend_csp.py` and the frontend's nginx configuration
- Related: [plan](../../plans/2026-10-22-vue-ui-kit-nuxt-ui.md);
  [[ADR-006]] (provenance), [[ADR-007]] (separate frontend templates),
  [[ADR-010]] (layered component model)

## Context

The Vue template ships shadcn-vue as its only component kit. The ownership
model keeps that kit in every generated tree: `src/shared/ui/` is
`generated`, the screens around it are `scaffold`, and the layouts and
component features import it.

A project that wants Nuxt UI 4 today installs it beside the generated kit and
hooks scaffold files to make the two coexist. Both kits define the same
Tailwind colour utilities from different tokens. The generated settings store
and Nuxt UI's colour mode both write `.dark`, each from its own storage key.
The generated screens keep rendering on shadcn-vue. Every hook is an edited
scaffold file, so every generator update that changes the same file proposes
it again as a sidecar.

Those clashes exist only because the template has one kit. Nuxt UI is a
complete kit with its own theming, colour mode, icons and overlay layer; it
works best adopted by its documented model, not adapted to shadcn-vue's.

## Decision

1. **One option, Vue only.** `frontend.ui_kit` takes `shadcn` (the default)
   or `nuxt-ui`. It travels like `frontend.layout`: `FrontendConfig.ui_kit`,
   the stack file, `--ui-kit`, the interactive prompt, the Copier variable
   `ui_kit`, `[forge.frontend] ui_kit` in `forge.toml` and the recipe. It is
   not an option-registry path, so `forge --schema` does not list it. Svelte
   and Flutter keep their kits; the asymmetry is deliberate, as in ADR-007.
2. **A registry of kits.** `forge/ui_kits.py` records each kit's frameworks,
   whether it is selectable, whether the AI chat UI renders on it, and the
   colour schemes it accepts. A kit can be registered unselectable, the way a
   layout can be registered with `supported = false`; `nuxt-ui` was, until the
   template rendered it.
3. **Compatibility is declared where each artifact lives.** A layout lists
   its kits in `layout.toml` (`ui_kits`), a component feature in
   `[feature.component]` (`ui_kits`, plus `replaced_by` naming the component a
   kit offers instead), a fragment on `Fragment.ui_kits`. Layouts and
   components default to `shadcn` only; fragments default to any kit. A
   declaration is checked against its files: a tree that claims `nuxt-ui`
   may not import the shadcn kit or use its colour classes. A fragment may
   ship a `files-<kit>/` tree whose files replace the same paths of `files/`
   for that kit, so one fragment serves both kits under one name.
4. **Refusals happen at plan time.** Before anything is written, forge refuses
   an unknown or unselectable kit, a kit on a template without kits, a layout
   that does not list the kit, chat on a kit without the chat UI, a colour
   scheme the kit does not accept, a component the kit cannot render (or a
   child it pulls in, reported through the selected component), and a
   fragment declaring other kits.
5. **Kit-specific files sit side by side.** The base template carries both
   kits under Copier conditional file names, and `src/shared/ui/` sits under a
   conditional directory name, so both variants of a screen are reviewed in
   one place and the shared plumbing stays single.
6. **Nuxt UI is adopted by the book.** Under `nuxt-ui`, Nuxt UI's colour mode
   is the one writer of `.dark`, the palette is fixed per project, and nothing
   of the shadcn-era theming (the settings store, `.oled`, the runtime
   schemes, the text-size axis) is carried over.
7. **Content security is repaired for both kits.** With `security.csp` on, the
   frontend's nginx (`nginx.conf` and the Helm chart's default server block)
   sends a per-request nonce policy with `index.html`, `$request_id` written
   over the placeholder the build stamps, and the defence-in-depth headers
   with every response. The app hands the nonce to whatever inserts `<style>`
   elements: radix-vue's `ConfigProvider` under `shadcn`; under `nuxt-ui`,
   `UApp` (Reka UI) and a `tags:resolve` hook on the head instance that renders
   Nuxt UI's palette, plus a `csp-nonce` meta tag with a `content` attribute
   for vaul-vue. zod parses without `eval`. Both Vue lanes run the browser
   suites against the production build under the policy and fail on any
   violation, with two exceptions named below. They run with the project's
   authentication setting, like every browser suite of the lanes; nothing
   serves `/auth/*` there, so the suites answer the session in the page
   (`tests/e2e/session.ts`). The sign-in flow through the gateway and Keycloak
   is a consumer's live suite to run under its real policy. A project that
   keeps its own policy in a header template names it in `csp-check.json`,
   and the check fails unless a canary the policy blocks is reported.
   `connect-src` admits the page's own origin and an external API's, not
   `ws:` or `wss:` at large. The policy covers the Vue
   template without the AI chat, which is what the lanes prove under it.
   (Revised 2026-10-26: the browser suites ran with authentication forced off,
   which broke a consumer's specs that mock the session.)

### What the policy leaves out

- **Trusted Types.** `require-trusted-types-for 'script'` is not sent. In Vue
  mode, Nuxt UI 4.12's stub reports the app as hydrating, so its colour plugin
  writes a temporary copy of the palette through `innerHTML` before its head
  entry renders; Trusted Types blocks that write and the app does not start.
  No option turns the copy off. Enforcing Trusted Types is an open point until
  Nuxt UI skips the copy in Vue mode.
- **Two upstream `<style>` elements without the nonce.** The same temporary
  palette copy, and the stylesheet with which VueUse's `useColorMode`
  suppresses transitions while the mode changes, are inserted without the
  nonce. The browser blocks both without visible effect: the palette applies
  from the head entry, which carries the nonce. The policy's `'report-sample'`
  identifies them, and the check admits exactly these two, by their exact
  samples, in a `nuxt-ui` project. The copy's sample is also the real
  palette's, so the Nuxt UI e2e suite asserts that the palette applies.
- **A nonce the page can read.** A browser hides the value of a `nonce`
  attribute from the page, so injected markup cannot read it through CSS
  attribute selectors or a dangling tag. vaul-vue reads it from the `content`
  attribute of the `csp-nonce` meta tag instead, so a `nuxt-ui` page carries
  it in the open. It is fresh for each response of a `no-store` page, which
  limits a leak to that page load. The copy can go when vaul-vue reads the
  `nonce` property.
- **Strict-Transport-Security.** nginx serves the frontend over plain HTTP
  behind a TLS terminator, and a browser keeps the header for as long as it
  says, subdomains included. The terminator sends it.
- **The AI chat.** Its MCP panels load the app's `entryUrl` in an iframe,
  usually from another origin, and the policy names no frame source, so
  `default-src 'self'` would block them. Which origins to admit is the
  project's to say, and no lane runs the chat, so a project with the chat is
  served without the policy, as before.

## Alternatives considered

### The kit as a layout overlay

Rejected. An overlay renders after the base and cannot remove the base's
`src/shared/ui/`, so the base would need the conditional anyway. A third
render stage would also have to interleave with the layout overlays.

### A separate Vue template for Nuxt UI

Rejected. ADR-007 separates templates because frameworks differ in idiom.
Two Vue templates would duplicate the router, auth, API client, stores, mocks
and tests to vary only the component layer.

### Nuxt UI wired by each consumer

Rejected. Consumer hooks are edited scaffold, which an update re-proposes as
sidecars whenever it changes those files, and the generated screens would stay
on shadcn-vue.

### Keeping the shadcn-era theming under Nuxt UI

Rejected. Turning Nuxt UI's colour mode off so the settings store keeps
writing `.dark` adapts the kit to a clash that only exists with shadcn-vue,
and leaves Nuxt UI's colour-mode components inert.

## Consequences

- Existing projects render as before. Their recipe gains the `ui_kit` field
  and their manifest a `ui_kit` line on the next update.
- New layouts and component features render with `shadcn` until they declare
  more, so a new kit can never be selected for something that ignores it.
- The kit-free components are fewer than their imports suggest: StatCard,
  StatusDot and ChatFirst use shadcn colour classes, so they declare `shadcn`
  only.
- Plugin layouts render with `shadcn` unless `add_frontend_layout` lists
  more in `ui_kits` (SDK 1.6).
- Every Vue project without the AI chat that has `security.csp` on starts
  serving an enforced policy on its next update. Code that inserts `<style>` elements or writes `style`
  attributes as markup is blocked, and origins the frontend calls must be
  listed in `connect-src`. Svelte, Flutter, plugin frontends and projects
  with the chat are served without a policy; the standalone snippet the option used to emit, which
  nothing included, is gone.
- Until Nuxt UI skips its temporary palette copy in Vue mode or gives it the
  nonce, and VueUse's transition guard takes one, the browser console of a
  `nuxt-ui` app shows their two blocked-style reports.
- Nuxt UI is pinned as a forge constant with a caret on the major; the
  vendored Nuxt UI agent skill follows its minor.

## References

- `plans/2026-10-22-vue-ui-kit-nuxt-ui.md` — the plan, its sequencing (F-01 to
  F-03) and the Nuxt UI facts it rests on.
- `forge/ui_kits.py` — the registry and the frontend checks.
- `tests/test_ui_kits.py`, `tests/test_ui_kit_coverage.py` — the refusals and
  the declaration lint.
- `forge/frontend_csp.py`, `tests/test_frontend_csp.py` — the policy and what
  serves it; `forge/quality/csp.py` — the lanes' content-security check.
- `tests/test_vue_ui_kit.py` — the rendered `nuxt-ui` application.
- Nuxt UI's Vue installation guide, `https://ui.nuxt.com/docs/getting-started/installation/vue`.
