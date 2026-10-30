---
name: forge-platform
description: Configure, generate, extend, test, or upgrade a Forge platform and recommend supported service technologies from workload requirements. Use in Forge-generated projects or when a user requests Forge.
---

Inspect `forge.toml` and `.forge/quality.json` in an existing project. Read
`forge --help`, `forge --list --format json`, and `forge --schema` from the
installed version before choosing options; capabilities vary across backends.

For technology selection, describe services in a YAML/JSON requirements file
and run `forge --recommend requirements.yaml --json`. See
[workloads.md](references/workloads.md) for the input shape and decision rules.
Respect explicit compatible language choices. Use the returned `config`, or
the individual `service_configs` when different services need different
options. Validate the proposal with `forge --config stack.yaml --plan --json`.
Forge options are project-wide; do not invent per-service option overrides.

Generate with `forge --config stack.yaml --yes --no-docker --json`. Use
`--dry-run` to inspect rendered output when useful. Discovery/planning does
not require LLM credentials. Do not put credentials in generation inputs.

To extend an existing platform, inspect provenance ownership:

- `generated`: change the upstream template/schema or implement a public port;
  do not patch, shadow, subclass to replace, or import private generated internals.
- `scaffold`: editable initial application code. Keep custom behavior in custom
  modules and register it from the composition root through public contracts.
- `user`: never replaced by the generator.

In a Vue frontend on the `nuxt-ui` kit, this project's setup takes precedence
over the `nuxt-ui` skill's installation, setup and theming steps. Installation
and setup are done: the Vite plugin, `@nuxt/ui/vue-plugin`, `UApp` and the
`lucide` icons are wired. Theme in the brand module, the frontend's
`src/custom/theme/`: palettes, icons and component overrides in
`nuxt-ui.config.ts`, which `vite.config.ts` passes to the plugin; Tailwind
theme and `--ui-*` variables in `tokens.css`. Colour mode is Nuxt UI's own,
backed by VueUse (`UColorModeSelect`, or `useColorMode` from `@vueuse/core`).

A Vue frontend on the `app-shell` application template (`frontend.app_template`)
takes its shell, menus and routing from the configuration: change
`frontend.layouts`, `routing`, `auth` or `extras` in the stack file and in the
recipe's `config.frontend`, then update; never edit `src/shared/application/`,
`src/shared/layouts/` or `src/shared/system/`. Put pages under `src/features/`;
compose their routes, the layout wrappers that fill the shell's slots (`logo`,
`header`, `actions`, `footer`, `aside`) and the menu actions in
`src/app/router/index.ts`; mark a route's menu item with `meta.menu`, supply
badge values with `provideMenuBadges`, and keep the authentication contract in
`src/app/auth.ts`.

Validate with `forge --quality architecture --project-path . --json`.
List test targets with `forge --quality inventory --project-path .` and run
`forge --quality test --project-path .`. Run the coverage gate with
`forge --quality coverage --project-path . --base-ref origin/main` for changes,
or without `--base-ref` for a new application. Coverage is strictly greater
than 80% per service; missing suites/reports are failures. Fix behavior and
tests instead of weakening exclusions, thresholds, or generated ownership.

For upgrades, inspect `forge --plan-update --project-path . --json`, then use
`forge --update --project-path . --json`. Review `.forge-merge` candidates. Acknowledge each with `forge --quality resolve
--subject RELATIVE_FILE --resolution keep|replace --project-path .`, then rerun
the update. `keep` preserves the current file, including a manual merge. Verify architecture and run the service suites
afterward. Legacy projects use `forge --quality migrate --project-path .` for
an ownership proposal; review modified files before adopting protection.

Report what changed, tests actually executed, coverage results, and remaining
failures. A compile check or a skipped suite is not test execution. Repository
settings disable AI authorship trailers; preserve the user's author identity.
