"""Template re-render driver — Copier base-template update tasks.

Split out from the original ``updater.py`` god module — see
:mod:`forge.sync.forge_to_project.updater` for the public surface.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from forge.config import BackendConfig
from forge.sync.manifest import ForgeTomlData


def _build_template_update_tasks(
    *,
    project_root: Path,
    data: ForgeTomlData,
    backends: list[BackendConfig],
) -> list[Any]:
    """Compare recorded vs. live template versions and enqueue tasks.

    For each language in :attr:`ForgeTomlData.template_versions`, look
    up the current template version from the registry (preferring
    ``_forge_template.toml`` under ``forge/templates/<dir>``). If the
    recorded version differs from the current, build a
    :class:`TemplateUpdateTask` for every target directory on disk that the
    template rendered: each backend of that language under
    ``services/<backend>/``, and each recorded frontend of that framework at
    its ``app_dir`` (falling back to ``apps/<framework>/`` or the first
    ``apps/<dir>/`` with Copier answers when the manifest records none).

    Returns the (possibly empty) list of tasks. The list is empty when
    every recorded version matches the live one — no Copier work is
    required in that case.
    """
    # Importing the generator's TEMPLATES_DIR + TEMPLATE_DIRS would
    # create a circular import (generator → updater hooks via the CLI),
    # so we resolve the templates root and the frontend dispatch
    # table from forge.templates directly.
    import forge as _forge  # noqa: PLC0415
    from forge.config import (  # noqa: PLC0415
        BACKEND_REGISTRY,
        FRONTEND_SPECS,
        FrontendFramework,
        resolve_backend_language,
    )
    from forge.sync.forge_to_project.template_update import (  # noqa: PLC0415
        TemplateUpdateTask,
    )
    from forge.sync.template_version import resolve_template_version  # noqa: PLC0415

    templates_root = Path(_forge.__file__).parent / "templates"
    # Mirror the generator's frontend dispatch so we resolve framework
    # → template_dir without importing the generator.
    frontend_dispatch: dict[str, str] = {
        FrontendFramework.VUE.value: "apps/vue-frontend-template",
        FrontendFramework.SVELTE.value: "apps/svelte-frontend-template",
        FrontendFramework.FLUTTER.value: "apps/flutter-frontend-template",
    }

    tasks: list[Any] = []
    backend_languages = {bc.language.value for bc in backends}

    for lang, recorded_version in sorted(data.template_versions.items()):
        # Resolve the template's path + current version.
        template_path: Path | None = None
        spec_default = "1.0.0"

        # Backend language? ``resolve_backend_language`` (not the
        # ``BackendLanguage`` constructor) so a plugin-registered language
        # in the manifest resolves to its sentinel and gets update-checked,
        # instead of silently falling into the ``None`` branch and being
        # skipped. A genuinely unknown value (plugin uninstalled) still
        # raises ValueError → None → skip.
        try:
            backend_lang = resolve_backend_language(lang)
        except ValueError:
            backend_lang = None  # type: ignore[assignment]
        if backend_lang is not None and lang in backend_languages:
            spec = BACKEND_REGISTRY[backend_lang]
            template_path = templates_root / spec.template_dir
            spec_default = spec.version
        elif lang in frontend_dispatch:
            template_path = templates_root / frontend_dispatch[lang]
        elif lang in FRONTEND_SPECS:
            fspec = FRONTEND_SPECS[lang]
            template_path = templates_root / fspec.template_dir
            spec_default = fspec.version

        if template_path is None or not template_path.is_dir():
            # Plugin template the registry no longer ships, or the
            # path drifted — leave the recorded version untouched and
            # skip silently. The verify command surfaces this kind of
            # drift separately.
            continue

        current_version = resolve_template_version(template_path, spec_default=spec_default)
        if current_version == recorded_version:
            continue

        # Resolve every target directory on disk: (dir, backend, frontend record).
        targets: list[tuple[Path, BackendConfig | None, Any]] = []
        if backend_lang is not None:
            for bc in backends:
                if bc.language.value == lang:
                    targets.append((project_root / "services" / bc.name, bc, None))
        else:
            # Frontend: each recorded app of this framework at its ``app_dir``
            # (Initiative #3, v4; every app from v5); a recorded app without
            # Copier answers is skipped. Only when the manifest records no app
            # of this framework (or none with an ``app_dir``), the
            # conventional ``apps/<framework_slug>/`` slot, then the first
            # ``apps/<dir>/`` with a ``.copier-answers.yml`` — the
            # pre-Init-#3 behavior.
            recorded = [fe for fe in data.frontends if fe.framework == lang and fe.app_dir]
            for entry in recorded:
                candidate = project_root / entry.app_dir
                if (candidate / ".copier-answers.yml").is_file():
                    targets.append((candidate, None, entry))
            apps = project_root / "apps"
            if not recorded and apps.is_dir():
                framework_dir = apps / lang
                if (framework_dir / ".copier-answers.yml").is_file():
                    targets.append((framework_dir, None, data.frontend))
                else:
                    for sub in sorted(apps.iterdir()):
                        if sub.is_dir() and (sub / ".copier-answers.yml").is_file():
                            targets.append((sub, None, data.frontend))
                            break

        seen: set[Path] = set()
        for target_dir, matched_backend, frontend in targets:
            if target_dir in seen or not target_dir.is_dir():
                continue
            seen.add(target_dir)
            if not (target_dir / ".copier-answers.yml").is_file():
                # Without answers, ``copier update`` has no input. Skip
                # silently — the project predates answer-file emission, or
                # the user removed it.
                continue

            # Two-stage (overlay) layouts: the project's ``.copier-answers.yml``
            # records only the overlay ``_src_path``, so the default
            # ``copier.run_update`` re-renders the overlay alone and silently
            # skips the shared base. Resolve the base template (if this
            # backend/frontend was generated from a two-stage variant) so
            # ``run_template_update`` re-renders it first.
            base_template_src = _resolve_base_template_src(
                templates_root=templates_root,
                lang=lang,
                backend_lang=backend_lang,
                matched_backend=matched_backend,
                frontend=frontend,
            )

            tasks.append(
                TemplateUpdateTask(
                    language=lang,
                    project_version=recorded_version,
                    current_version=current_version,
                    target_dir=target_dir,
                    template_src=template_path,
                    base_template_src=base_template_src,
                )
            )
    return tasks


def _resolve_base_template_src(
    *,
    templates_root: Path,
    lang: str,
    backend_lang: Any,
    matched_backend: BackendConfig | None,
    frontend: Any,
) -> Path | None:
    """Resolve the shared-base template for a two-stage layout, if any.

    A two-stage backend app-template variant or frontend layout variant
    records a non-empty ``base_template_dir`` in its registry entry. When
    present, return the absolute path to that base under
    ``templates_root``; otherwise ``None`` (self-contained single render).
    """
    base_dir = ""
    if backend_lang is not None and matched_backend is not None:
        # Backend: resolve the app-template variant the backend was
        # generated from and read its shared-base dir.
        from forge.backend_app_templates import (  # noqa: PLC0415
            DEFAULT_BACKEND_TEMPLATE,
            get_backend_application_template,
        )

        variant_name = matched_backend.app_template or DEFAULT_BACKEND_TEMPLATE
        variant = get_backend_application_template(backend_lang, variant_name)
        if variant is not None:
            base_dir = variant.base_template_dir
    elif backend_lang is None:
        # Frontend: resolve the layout variant from the recorded
        # (framework, layout) and read its shared-base dir.
        from forge.config import resolve_frontend_framework  # noqa: PLC0415
        from forge.layout_variants import DEFAULT_LAYOUT, get_layout_variant  # noqa: PLC0415

        try:
            framework = resolve_frontend_framework(lang)
        except ValueError:
            framework = None  # type: ignore[assignment]
        if framework is not None:
            layout_name = getattr(frontend, "layout", "") or DEFAULT_LAYOUT
            variant = get_layout_variant(framework, layout_name)
            if variant is not None:
                base_dir = variant.base_template_dir

    if not base_dir:
        return None
    base_path = templates_root / base_dir
    if not base_path.is_dir():
        return None
    return base_path
