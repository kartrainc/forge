"""Backend application-template variants — the selectable ``app_template`` shapes.

This is the backend analogue of :mod:`forge.layout_variants` (the frontend
``--layout`` system). A :class:`LayoutVariant` records which Copier *template*
renders a given ``(framework, layout)`` UI shell; a
:class:`BackendApplicationTemplate` records which Copier template renders a
given ``(language, variant)`` *service shape* — the user-facing application
template chosen via ``BackendConfig.app_template``.

It is deliberately **separate** from :data:`forge.config.BACKEND_REGISTRY`
(:class:`~forge.config.BackendSpec`), which ties a language to its baseline
template, toolchain, and CLI prompt metadata. The default variant
(``crud-service``) reuses that baseline template verbatim, so selecting it
reproduces pre-app-template output byte-for-byte (the golden gate).

Composition model. Unlike frontend layouts — which are thin shells that
overlay a shared base (two-stage render) — a backend variant is a *whole
service*. The recommended shape is therefore **self-contained**
(``base_template_dir == ""``, single render): the built-in ``crud-service``
ships exactly that way, pointing at the existing per-language baseline
template. A variant may still opt into a two-stage shared-base + thin-delta
render by setting ``base_template_dir`` when it is genuinely a small delta on
a base, mirroring the frontend mechanism — but for full services the
self-contained shape is preferred.

**Discovery.** Variants are auto-discovered from manifest files at
``forge/templates/services/<language>/<variant>/template.toml`` (mirroring the
layout-discovery pattern). Adding a variant is a drop-in: ship a manifest +
its template; no code change here. Additionally, a built-in ``crud-service``
variant is registered for every language in :data:`forge.config.BACKEND_REGISTRY`
whose ``template_dir`` is that language's existing baseline template (a single
self-contained render). Plugins can also register at runtime via
:meth:`forge.api.ForgeAPI.add_backend_application_template`.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from forge.config import BackendLanguage, _PluginLanguage

    # Registry key: a built-in backend language or a plugin-registered one.
    LanguageKey = BackendLanguage | _PluginLanguage

#: The default application template — today's single per-language baseline
#: service. Selecting it reproduces pre-app-template output byte-for-byte.
DEFAULT_BACKEND_TEMPLATE = "crud-service"

#: Root of the service-template tree. The same directory holds the legacy
#: baseline templates (``services/<lang>-service-template``) and the new
#: per-language variant manifests (``services/<lang>/<variant>/template.toml``).
_SERVICES_ROOT = Path(__file__).parent / "templates" / "services"


@dataclass(frozen=True)
class BackendApplicationTemplate:
    """One selectable ``(language, variant)`` backend application template.

    Attributes:
        language: The backend language this variant targets (a built-in
            :class:`~forge.config.BackendLanguage` member or a plugin
            ``_PluginLanguage`` sentinel).
        variant: The application-template slug (e.g. ``"crud-service"``,
            ``"worker"``) — the ``BackendConfig.app_template`` value.
        template_dir: The variant's Copier template, **relative to
            ``forge/templates``** (e.g. ``"services/python/worker"``). For a
            self-contained variant this is the whole template; for a
            two-stage variant it is the thin delta overlay.
        display_label: Human-readable name for CLI / interactive listings.
        supported: ``False`` hides the variant from selection without
            unregistering it.
        base_template_dir: Optional shared-base template (relative path)
            rendered *before* ``template_dir`` overlays it. Empty ⇒
            self-contained single render (the preferred shape for whole
            services, and how ``crud-service`` ships).
        requires_services: Infra services this variant needs at boot (e.g.
            ``("redis",)`` for ``tenant-management-service``, whose outbox
            relay connects to Redis on startup). ``render_compose`` adds a
            ``depends_on: <svc>: condition: service_healthy`` edge for each
            one that is actually rendered, so the service doesn't crash-loop
            racing an infra container that isn't up yet.
        uses_database: ``False`` for a service shape with no database of its
            own (e.g. Node ``http-service``): no database, migrate step or
            DB URL is wired for it, and its template renders with
            ``use_database=false``.
    """

    language: LanguageKey
    variant: str
    template_dir: str
    display_label: str
    supported: bool = True
    base_template_dir: str = ""
    requires_services: tuple[str, ...] = ()
    uses_database: bool = True


BACKEND_APPLICATION_TEMPLATES: dict[tuple[LanguageKey, str], BackendApplicationTemplate] = {}


def register_backend_application_template(template: BackendApplicationTemplate) -> None:
    """Register a :class:`BackendApplicationTemplate`. Raises on a duplicate key."""
    key = (template.language, template.variant)
    if key in BACKEND_APPLICATION_TEMPLATES:
        raise ValueError(
            f"BackendApplicationTemplate {_lang_value(template.language)}/"
            f"{template.variant!r} is already registered"
        )
    BACKEND_APPLICATION_TEMPLATES[key] = template


def get_backend_application_template(
    language: LanguageKey, variant: str
) -> BackendApplicationTemplate | None:
    """Return the variant for ``(language, variant)``, or ``None``."""
    return BACKEND_APPLICATION_TEMPLATES.get((language, variant))


def backend_uses_database(language: LanguageKey, variant: str | None) -> bool:
    """Whether a backend's app template owns a database (unknown variants do)."""
    template = get_backend_application_template(language, variant or DEFAULT_BACKEND_TEMPLATE)
    return template.uses_database if template is not None else True


def available_backend_templates(language: LanguageKey) -> tuple[str, ...]:
    """Return the sorted, *supported* variant slugs for ``language``."""
    return tuple(
        sorted(
            variant
            for (lang, variant), t in BACKEND_APPLICATION_TEMPLATES.items()
            if lang == language and t.supported
        )
    )


def all_backend_template_names() -> tuple[str, ...]:
    """Return every registered variant slug across languages (sorted, deduped)."""
    return tuple(sorted({variant for (_lang, variant) in BACKEND_APPLICATION_TEMPLATES}))


def _lang_value(language: LanguageKey) -> str:
    return getattr(language, "value", str(language))


def _language_from_value(value: str) -> LanguageKey | None:
    """Resolve a manifest's ``language`` string to a registry key.

    Returns ``None`` for an unknown value so discovery can skip a stray
    manifest rather than raising at import time.
    """
    from forge.config import PLUGIN_LANGUAGES, BackendLanguage  # noqa: PLC0415

    for member in BackendLanguage:
        if member.value == value:
            return member
    # A plugin language may not be registered yet at discovery time; only
    # resolve to one that already exists.
    if value in PLUGIN_LANGUAGES:
        return PLUGIN_LANGUAGES[value]
    return None


def _register_builtin_crud_services() -> None:
    """Register the built-in ``crud-service`` variant for every language.

    Its ``template_dir`` is the language's existing baseline template from
    :data:`forge.config.BACKEND_REGISTRY` and ``base_template_dir`` is empty
    — a single self-contained render, byte-identical to the pre-app-template
    path. This is the backend analogue of the built-in ``sidebar`` layout.
    """
    from forge.config import BACKEND_REGISTRY  # noqa: PLC0415

    for language, spec in BACKEND_REGISTRY.items():
        key = (language, DEFAULT_BACKEND_TEMPLATE)
        if key in BACKEND_APPLICATION_TEMPLATES:
            continue
        register_backend_application_template(
            BackendApplicationTemplate(
                language=language,
                variant=DEFAULT_BACKEND_TEMPLATE,
                template_dir=spec.template_dir,
                display_label=f"{spec.display_label} — CRUD service",
                supported=True,
                base_template_dir="",
            )
        )


def _discover_manifests() -> None:
    """Discover variants from ``services/<language>/<variant>/template.toml``.

    Only directories that ship a ``template.toml`` are treated as variant
    manifests; the legacy baseline trees (``services/<lang>-service-template``)
    have no manifest and are skipped here — they're registered as
    ``crud-service`` from :data:`forge.config.BACKEND_REGISTRY` instead.
    """
    if not _SERVICES_ROOT.is_dir():
        return
    for manifest in sorted(_SERVICES_ROOT.glob("*/*/template.toml")):
        try:
            data = tomllib.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            continue
        template = data.get("template")
        if not isinstance(template, dict):
            continue
        variant = template.get("variant")
        lang_value = template.get("language")
        template_dir = template.get("template_dir")
        if not variant or not lang_value or not template_dir:
            continue
        language = _language_from_value(str(lang_value))
        if language is None:
            continue
        requires_raw = template.get("requires_services", [])
        requires_services = (
            tuple(str(s) for s in requires_raw) if isinstance(requires_raw, list) else ()
        )
        uses_database = template.get("uses_database", True)
        if not isinstance(uses_database, bool):
            # A string such as "false" would read as true and wire a database: skip the
            # manifest like any other malformed one rather than guess.
            continue
        register_backend_application_template(
            BackendApplicationTemplate(
                language=language,
                variant=str(variant),
                template_dir=str(template_dir),
                display_label=str(template.get("display_label", variant)),
                supported=bool(template.get("supported", True)),
                base_template_dir=str(template.get("base", "")),
                requires_services=requires_services,
                uses_database=uses_database,
            )
        )


def _discover() -> None:
    """Register built-in ``crud-service`` variants, then manifest-discovered ones."""
    _register_builtin_crud_services()
    _discover_manifests()


def _reset_for_tests() -> None:
    """Clear the registry and re-discover built-ins (test isolation)."""
    BACKEND_APPLICATION_TEMPLATES.clear()
    _discover()


_discover()
