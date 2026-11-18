# Forge: the app shell's page, footer, Escape rule and folded sub-menus

Base: ef9bff1e (`kartrainc/forge` main, PR #20).

Date: 2026-11-18. Status: implemented on `feat/shell-s5-s7`, 2026-11-18.

Requested scope: bring the console shell of `kartrainc/kartra-ai` (its design intake) into the generated
`header-sidebar` shell, so that its wrapper stops working around the frame. Every item is additive; its default keeps
what the shell rendered before, except the two changes named under Changed.

| Item | Change in `HeaderSidebarShell.vue` | Default |
| --- | --- | --- |
| `header.close` | the Close button beside a narrow screen's opened header is `v-if="header.close !== false"` | shown |
| `page.main` | `main` takes `page.main ?? 'flex-1 p-4 sm:p-6'`: a project's classes replace the growth too | `flex-1 p-4 sm:p-6` |
| page column | `relative` and `data-shell="page"` on the scrolling column | always |
| footer | the `footer` slot gets `footerClass` and the shell renders the `footer` element only for its default menu | default menu in a `footer` |
| Escape | with `asideSlideover.dismissible === false` and the panel open, its layer is no open layer to leave the key to | off |
| `foldedSubmenu` | `{ mode: 'popover' \| 'inline', ui? }`; inline lists the children after their parent in the folded menu, `aria-expanded` on the parent, shut rows at no height and `invisible`, over the layout's open sub-menus | `popover` |
| edge toggles | last in the DOM, in zero-width layers (`order-1`, `order-3`) beside the page (`order-2`) and the panel (`order-4`) | always |
| `collapsedSize` | 3.5 rem | was 4 |

Changed: a `footer` slot no longer has a `footer` element drawn around it (it draws its own with `footerClass`), and the
edge toggles leave the page's box for their layers. Neither changes what a default shell looks like.

Tests: `HeaderSidebarShell.test.ts` (rendered with the `py_vue_app_shell` scenario's stack): the footer's classes and
element, the page column, the toggles' DOM order, `header.close`, the Escape rule, the inline sub-menu (shut, opened,
the project's row classes, the open sidebar untouched) and the popover default.
