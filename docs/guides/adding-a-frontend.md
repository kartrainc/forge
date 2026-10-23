# Adding a frontend framework

This guide covers the steps to register a new frontend framework in forge,
either as a core-shipped framework or as a plugin. It mirrors
`docs/guides/adding-a-backend.md` but focuses on the quirks specific to frontends
(the `_subdirectory` contract, the `FrontendLayout` for codegen, and the
generator's frontend dispatch).

## When to add one

- You need a new JS/TS framework forge doesn't ship (Solid, Qwik, Remix,
  Astro, React/Next, …).
- You need an alternative Flutter template, or a Kotlin Multiplatform /
  SwiftUI / native-mobile template with a different directory layout.
- You want to prototype a framework before proposing it for core.

If the framework is close enough to an existing template that a single
Copier variable would cover the delta, prefer extending the existing
template instead.

Another component library for the Vue template is not a new framework: it is
a UI kit (`frontend.ui_kit`), rendered from the Vue template's kit-specific
files beside the shared ones. See [UI kits](frontend-layouts.md#ui-kits) and
ADR-011.

## The three registration points

A frontend is fully described by three pieces of metadata:

1. **A framework identifier** (`FrontendFramework` enum member or a
   plugin-registered `_PluginFramework` sentinel) — the wire value
   that appears in CLI flags and config files.
2. **A `FrontendSpec`** — static metadata: Copier template directory,
   display label, and whether the template uses Copier's `_subdirectory`
   key.
3. **A `FrontendLayout`** (only if the framework participates in
   schema-first codegen) — the paths where generated UI-protocol types,
   canvas manifest, and shared enums land. See `forge/frontends.py` and
   `docs/architecture/generator.md` for the codegen pipeline that consumes these.

Core frontends (Vue, Svelte, Flutter) short-circuit the spec registry
and use `TEMPLATE_DIRS` in `forge/generator.py`, but they still appear
in `FRONTEND_LAYOUTS` for codegen. Plugin frontends use
`FRONTEND_SPECS[value]` via `api.add_frontend(...)`.

## The `_subdirectory` contract

`FrontendSpec.uses_subdirectory` controls where the generator asks
Copier to render your template:

| `uses_subdirectory` | Copier `_subdirectory:` key | Destination passed to Copier |
| --- | --- | --- |
| `True` (default) | Declared in `copier.yml` | `project_root/apps/<frontend_slug>/` |
| `False` | Not declared | `project_root/apps/` (the template itself owns the next directory level, e.g. `{{project_slug}}/`) |

Most Copier templates declare `_subdirectory: template` so everything
under `template/` renders into the destination directly. That's the
default and the least-surprising path for plugins. Flutter is the
exception: its template has a `{{project_slug}}/` layer at the top and
no `_subdirectory:` key, so the generator points Copier at `apps/` and
lets the template create the inner directory. If your template does the
same, set `uses_subdirectory=False`:

```python
# In your plugin's register(api) entry point:
from forge.config import FrontendSpec

api.add_frontend(
    "flutter_mobile_v2",
    FrontendSpec(
        template_dir="apps/flutter-mobile-v2-template",
        display_label="Flutter Mobile v2",
        uses_subdirectory=False,
    ),
)
```

`forge.config.frontend_uses_subdirectory(framework)` is the helper the
generator uses internally; you shouldn't need to call it from plugin
code, but it exists if you need to branch on the same flag (e.g. in a
custom doctor check).

## Minimum checklist

1. Drop your Copier template under `forge/templates/apps/<slug>-frontend-template/`
   (or ship it in your plugin package and point `template_dir` at an
   absolute path).
2. Register the framework: `api.add_frontend(wire_value, FrontendSpec(...))`.
3. Register the layout for codegen (optional, only if you want UI
   protocol / canvas / shared-enum codegen):

   ```python
   from forge.frontends import FrontendLayout, register_frontend_layout
   register_frontend_layout(
       FrontendLayout(
           framework=resolve_frontend_framework(wire_value),
           ui_protocol_path="src/lib/ui_protocol.gen.ts",
           ui_protocol_emitter="typescript",
           canvas_manifest_path="public/canvas.manifest.json",
           shared_enums_dir="src/lib/enums",
           shared_enums_emitter="typescript",
       )
   )
   ```

4. Verify locally: `forge --config my_plugin_scenario.yaml --output /tmp/x`
   and inspect the generated tree.

## Common gotchas

- **Wrong subdirectory flag → phantom files**. If you set
  `uses_subdirectory=True` but your template omits `_subdirectory:`,
  Copier renders the template root (including `template/`,
  `copier.yml`) into your `apps/<frontend_slug>/` destination.
  Conversely, setting it False for a template that does declare
  `_subdirectory:` produces an empty `apps/<frontend_slug>/` plus the
  actual files in `apps/`.
- **Collision with built-ins**. `api.add_frontend("vue", ...)` is
  rejected with a `PluginError`; pick a distinct wire value.
- **Layout vs. spec mixup**. The `FrontendLayout` controls codegen
  output paths; the `FrontendSpec` controls where Copier renders the
  base template. Both are often needed but serve different pipelines.

## See also

- `forge/config/_frontend.py` — `FrontendSpec`, `frontend_uses_subdirectory`,
  `FRONTEND_SPECS`, `resolve_frontend_framework`.
- `forge/frontends.py` — `FrontendLayout`, `FRONTEND_LAYOUTS`,
  `register_frontend_layout`.
- `forge/generator.py` — `_generate_frontend` consumes the above.
- `tests/test_frontend_subdirectory.py` — behavior lock-in.
- `tests/test_plugin_frontend.py` — plugin registration lifecycle.
