# Plugin SDK Changelog

This file tracks changes to the **plugin SDK surface** — the names and
signatures listed in ``forge/api.py``'s ``__all__``. The SDK version
lives at ``forge.api.SDK_VERSION`` and is independent of the forge
package version: a forge package release that doesn't touch the API
surface leaves SDK_VERSION unchanged.

Plugin authors target the SDK via ``api.require_sdk(">=X.Y")`` at the
top of their ``register()`` function. Bumps to SDK_VERSION require a
matching entry below; ``tests/test_sdk_version.py::test_sdk_changelog_exists``
asserts this file is present, and PRs that mutate ``forge.api.__all__``
without an entry fail review.

Versions follow ``MAJOR.MINOR`` only — no patch component, no
pre-release labels. SDK MAJOR aligns with the plugin compat boundary
(plugins may need code changes); MINOR signals additive surface
(plugins keep working, can opt into new APIs).

## 1.6 (2026-10, with forge 1.x)

Status: **provisional** — additive; existing plugins keep working unchanged.

Added:

- ``ForgeAPI.add_frontend_layout(..., ui_kits=("shadcn",))`` — the Vue UI
  kits (``forge/ui_kits.py``) a layout's files render with, as the
  ``ui_kits`` key of a ``layout.toml`` declares them. The default keeps an
  existing plugin layout on the default kit; a project on another kit
  (``frontend.ui_kit``) is refused the layout before anything is written
  (ADR-011). Component features declare their kits in
  ``[feature.component]`` and fragments on ``Fragment.ui_kits``; both landed
  with the option and need no SDK change.

## 1.5 (2026-06, with forge 1.x)

Status: **provisional** — additive; existing plugins keep working unchanged.

Added:

- ``ForgeAPI.add_platform_template(name, display_label, description, *,
  include_keycloak=False, options=None, backends=(), frontend=None,
  database_mode=None)`` — registers a selectable ``--platform`` preset (the
  ``ProjectConfig.platform_template`` choice). The platform-scale analogue of
  ``add_frontend_layout`` / ``add_backend_application_template``: a
  ``PlatformTemplate`` (``forge/platform_templates.py``) bundles option
  overrides + per-backend ``app_template``/``depends_on`` assignments + an
  optional frontend. The CLI builder deep-merges the preset's config dict *under*
  the user's config (lowest-priority layer), so user CLI flags and config-file
  values always win — selecting no platform is a no-op, keeping default output
  byte-identical. Built-in presets (``monolithic``, ``microservices``,
  ``headless-api``) are discovered from
  ``forge/templates/platforms/<name>/platform.toml``.

## 1.4 (2026-06, with forge 1.x)

Status: **provisional** — additive; existing plugins keep working unchanged.

Added:

- ``ForgeAPI.add_backend_application_template(language, variant,
  template_dir, display_label, *, base_template_dir="", supported=True)`` —
  registers a selectable backend application template (the
  ``BackendConfig.app_template`` choice) for a built-in or plugin backend
  language. The backend analogue of ``add_frontend_layout``: a
  ``(language, variant)`` ``BackendApplicationTemplate``
  (``forge/backend_app_templates.py``) maps to a Copier *service* template;
  the generator dispatches on it. The default ``crud-service`` variant is the
  language's existing baseline template, rendered self-contained (single
  render), so default output is byte-identical. ``base_template_dir`` enables a
  two-stage shared-base + thin-delta render for variants that are a genuine
  delta; for whole services the self-contained shape (empty ``base``) is
  preferred. Distinct from ``BackendSpec`` in ``forge/config/_backend.py``
  (language → baseline template + toolchain + prompts).

## 1.3 (2026-06, with forge 1.x)

Status: **provisional** — additive; existing plugins keep working unchanged.

Added:

- ``ForgeAPI.add_frontend_layout(framework, name, template_dir,
  display_label, *, base_template_dir="", supported=True)`` — registers a
  selectable UI app-shell layout (the ``--layout`` choice) for a built-in or
  plugin frontend. A ``(framework, layout)`` ``LayoutVariant``
  (``forge/layout_variants.py``) maps to a Copier template; the generator
  dispatches on it. ``base_template_dir`` enables the two-stage shared-base +
  thin-variant render (otherwise a self-contained single render). Distinct
  from the codegen ``FrontendLayout`` in ``forge/frontends.py`` (emit paths).

## 1.2 (2026-05, with forge 1.2.0-alpha.x)

Status: **provisional** — the new surfaces are additive but the
contracts may grow in a 1.3 minor before promotion to stable.

Added:

- ``ForgeAPI.add_injector(suffix: str, injector: Injector)`` —
  Pillar A.1, pluggable per-suffix injector dispatch. Replaces the
  hardcoded ``if/elif`` suffix chain in
  ``forge/appliers/injection.py:_dispatch_injector`` with a module-
  level registry at ``forge/injectors/_registry.py``. Built-ins
  (``.py`` / ``.pyi`` → LibCST, TS family → regex/ts-morph, ``*`` →
  sentinel text) seed at import time; plugins register new file
  types via ``add_injector``. Unblocks polyglot backend plugins that
  want to ship ``.go`` / ``.kt`` / ``.rs`` AST injectors without
  forking forge.

  The ``Injector`` contract is the same positional signature every
  existing injector exposes:
  ``(file: Path, feature_key: str, marker: str, snippet: str,
  position: str) -> None``. Provisional in 1.2 — the callable may
  grow a return value (e.g. structured diff for telemetry) in a
  later minor; the positional signature is locked.

- ``ForgeAPI.add_hook(hook: PhaseHook)`` — Pillar A.3, register a
  :class:`forge.hooks.PhaseHook` to observe generator phases.
  Callbacks (``on_phase_start`` / ``on_phase_end`` /
  ``on_generate_complete``) fire from the existing
  :func:`forge.logging.phase_timer` contexts that already wrap every
  phase, so no plumbing change is required to add new observability
  surfaces. Use cases: telemetry sinks, SBOM emitters, supply-chain
  signers, post-``forge new`` shell scripts. Hook exceptions are
  swallowed + logged inside the fire helpers so a buggy plugin
  cannot crash generation.

- ``forge.hooks`` module — the ``PhaseHook`` protocol itself,
  ``register_hook`` for direct in-tree callers, and the testing
  helper ``reset_hooks_for_tests``. ``forge.plugins.reset_for_tests``
  already calls the latter so test suites using the existing plugin
  reset fixture get hook isolation for free.

## 1.1 (2026-04, with forge 1.1.0-alpha.x)

Status: **stable**.

Added:

- ``ForgeAPI.require_sdk(spec: str)`` — plugin SDK version negotiation.
  Plugins call this at the top of ``register()`` to fail fast on an
  incompatible host instead of crashing with a confusing
  ``AttributeError`` when reaching for a method the host doesn't
  ship. ``spec`` is a comma-separated list of clauses
  (``">=1.1"``, ``">=1.1, <2.0"``).
- ``forge.api.SDK_VERSION`` — the version constant itself, exposed for
  plugins that want to introspect rather than constrain.
- ``ForgeAPI.add_service(capability: str, template: ServiceTemplate)``
  — register a docker-compose service keyed by capability. (Lifted
  from 1.1.0-alpha.1 release notes; the surface change is captured
  here as the first SDK_CHANGELOG entry.)

Changed:

- ``forge.fragments`` is now a namespace package (was the legacy
  monolithic ``forge/fragments.py``). All previously-public names
  (``Fragment``, ``FragmentImplSpec``, ``FRAGMENT_REGISTRY``,
  ``register_fragment``, etc.) are re-exported from the package
  root, so plugin imports of the form ``from forge.fragments import
  X`` keep working unchanged.
- ``forge.config`` is similarly a namespace package now. Same
  re-export contract — every name plugins consumed (``BackendLanguage``,
  ``ProjectConfig``, ``BACKEND_REGISTRY``, ``register_backend_language``,
  etc.) is exposed at the package root.

## 1.0 (2026-Q1, with forge 1.0.0a1 → 1.1.0-alpha.0)

Initial public SDK surface. Captures the names that landed across the
1.0.0 alpha series — recorded retroactively here so future bumps have
a baseline to diff against.

Public names:

- ``ForgeAPI`` (since 1.0.0a1)
- ``ForgeAPI.add_option`` (since 1.0.0a1)
- ``ForgeAPI.add_fragment`` (since 1.0.0a1)
- ``ForgeAPI.add_backend`` (since 1.0.0a2)
- ``ForgeAPI.add_frontend`` (since 1.0.0a4)
- ``ForgeAPI.add_command`` (since 1.0.0a4)
- ``ForgeAPI.add_emitter`` (since 1.0.0a1, **provisional**)
- ``PluginRegistration`` (since 1.0.0a1)
