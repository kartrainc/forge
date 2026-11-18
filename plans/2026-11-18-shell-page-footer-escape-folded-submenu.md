# Forge: the app shell's page, footer, Escape rule and folded sub-menus

Base: ef9bff1e (`kartrainc/forge` main, PR #20).

Date: 2026-11-18. Status: implemented on `feat/shell-s5-s7`, 2026-11-18.

Requested scope: bring the console shell of `kartrainc/kartra-ai` (its design intake) into the generated
`header-sidebar` shell, so that its wrapper stops working around the frame. Every item is additive; its default keeps
what the shell rendered before, except the changes named under Changed.

| Item | Change in `HeaderSidebarShell.vue` | Default |
| --- | --- | --- |
| `header.close` | the Close button beside a narrow screen's opened header is `v-if="header.close !== false"` | shown |
| `page.main` | `main` takes `page.main ?? 'flex-1 p-4 sm:p-6'`: a project's classes replace the growth too (**Changed** for a project that passes `page.main`) | `flex-1 p-4 sm:p-6` |
| page column | `relative` and `data-shell="page"` on the scrolling column | always |
| footer | the `footer` slot gets `footerClass` and the shell renders the `footer` element only for its default menu | default menu in a `footer` |
| Escape | with `asideSlideover.dismissible === false` and the panel open, its layer is no open layer to leave the key to, and only the panel's own cancellation of the key is discounted: the panel's `escapeKeyDown` handler (ahead of Nuxt UI's) marks the event when nothing had prevented it, so an Escape another control consumed keeps the header open | off |
| `foldedSubmenu` | `{ mode: 'popover' \| 'inline', ui? }`; inline lists the children after their parent in the folded menu, `aria-expanded` on the parent, shut rows at no height and `invisible`, over the layout's open sub-menus | `popover` |
| edge toggles | last in the DOM, in zero-width layers (`order-1`, `order-3`) beside the page (`order-2`) and the panel (`order-4`) | always |
| `collapsedSize` | 3.5 rem, with `min-w-14` on the sidebar's root, lowering Nuxt UI's `min-w-16` so the folded sidebar renders at the size it is given | was 4 |

Changed:

- a project that passes `page.main` loses `flex-1` unless its classes say it: its footer then follows the content on a
  short page instead of sitting at the bottom. Migration: add `flex-1` to `page.main` to keep the old layout.
- a `footer` slot no longer has a `footer` element drawn around it (it draws its own with `footerClass`), and the
  edge toggles leave the page's box for their layers. Neither changes what a default shell looks like.
- `collapsedSize` defaults to 3.5 rem (was 4) and the sidebar's root takes `min-w-14`, so a default folded sidebar is
  3.5 rem wide, not 4; a project that wants the old width passes `sidebar: { collapsedSize: 4 }`.

Tests: `HeaderSidebarShell.test.ts` (rendered with the `py_vue_app_shell` scenario's stack): the footer's classes and
element, the page column, the toggles' DOM order, `header.close`, the Escape rule (a control's cancelled Escape keeps the header open beside the kept panel), the row's single parent for the layers, the page and the panel, the folded root's `min-w-14`, the inline sub-menu (shut, opened,
the project's row classes, the open sidebar untouched) and the popover default.
