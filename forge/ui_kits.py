"""UI kits of the Vue template — the component library a project renders on.

``frontend.ui_kit`` selects one. ``shadcn`` is the shadcn-vue kit the Vue
template has always shipped and stays the default. ``nuxt-ui`` is Nuxt UI 4
(``plans/2026-10-22-vue-ui-kit-nuxt-ui.md``): the template renders its own
screens on it, with the version held in :data:`NUXT_UI_VERSION`. Svelte and
Flutter keep their own kits, so the option applies to the Vue template only
(ADR-011).

Compatibility is declared where each artifact lives: a layout lists its kits in
``layout.toml`` (``ui_kits``), a component feature in ``[feature.component]``
(``ui_kits``, default ``["shadcn"]``), a fragment on
:attr:`forge.fragments.Fragment.ui_kits`. This module checks the
frontend-local combinations (kit, layout, chat, colour scheme) and formats the
refusals; :mod:`forge.capability_resolver` checks components and fragments.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from forge.config._frontend import FrontendFramework

if TYPE_CHECKING:
    from forge.config import FrontendConfig

#: The kit every existing project renders on.
DEFAULT_UI_KIT = "shadcn"

#: The ``@nuxt/ui`` release the ``nuxt-ui`` kit is written against; the
#: template depends on it with a caret, so a project takes later 4.x minors.
NUXT_UI_VERSION = "4.12.0"

#: The official Nuxt UI agent skill, vendored in ``forge/templates/_common/skills/
#: nuxt-ui`` from ``skills/nuxt-ui`` of github.com/nuxt/ui at the release tag of
#: :data:`NUXT_UI_VERSION`, with the repository's MIT licence. A ``nuxt-ui``
#: project gets it in ``.agents/skills`` and ``.claude/skills``. Re-vendor it
#: when the version moves (docs/guides/agentic-usage.md).
NUXT_UI_SKILL_TAG = "v4.12.0"
NUXT_UI_SKILL_COMMIT = "b1053b8508078274326aa44bb322076ca135aedf"

#: Nuxt UI's MCP server, named in a ``nuxt-ui`` project's ``.mcp.json`` (Claude
#: Code) and ``.codex/config.toml`` (Codex).
NUXT_UI_MCP_URL = "https://ui.nuxt.com/mcp"

#: The colour schemes the Vue template offers (``default_color_scheme``).
COLOR_SCHEMES: tuple[str, ...] = (
    "blue",
    "indigo",
    "teal",
    "green",
    "deepPurple",
    "red",
    "amber",
    "cyan",
)

#: Tailwind CSS's default palettes; Nuxt UI's ``ui.colors`` take these names.
TAILWIND_PALETTES: tuple[str, ...] = (
    "slate",
    "gray",
    "zinc",
    "neutral",
    "stone",
    "red",
    "orange",
    "amber",
    "yellow",
    "lime",
    "green",
    "emerald",
    "teal",
    "cyan",
    "sky",
    "blue",
    "indigo",
    "violet",
    "purple",
    "fuchsia",
    "pink",
    "rose",
)

# The template's scheme names that are not Tailwind palette names.
_PALETTE_FOR_SCHEME = {"deepPurple": "violet"}


@dataclass(frozen=True)
class UiKit:
    """One selectable component library.

    Attributes:
        name: The ``frontend.ui_kit`` value.
        display_label: Human-readable name for prompts and errors.
        frameworks: The frontend frameworks whose template renders this kit.
        supported: ``False`` registers the kit without making it selectable.
        chat: Whether the AI chat UI (``include_chat``) renders on this kit.
        color_schemes: The ``default_color_scheme`` values this kit accepts.
    """

    name: str
    display_label: str
    frameworks: tuple[FrontendFramework, ...]
    supported: bool = True
    chat: bool = True
    color_schemes: tuple[str, ...] = COLOR_SCHEMES


UI_KITS: dict[str, UiKit] = {
    "shadcn": UiKit(
        name="shadcn",
        display_label="shadcn-vue (Radix Vue + Tailwind CSS 4)",
        frameworks=(FrontendFramework.VUE,),
    ),
    "nuxt-ui": UiKit(
        name="nuxt-ui",
        display_label="Nuxt UI 4 (Reka UI + Tailwind CSS 4)",
        frameworks=(FrontendFramework.VUE,),
        # The AI chat UI is built on shadcn-vue until its Nuxt UI port.
        chat=False,
        color_schemes=COLOR_SCHEMES + tuple(p for p in TAILWIND_PALETTES if p not in COLOR_SCHEMES),
    ),
}


#: Every ``default_color_scheme`` value some kit accepts, for the CLI's
#: choices; :func:`check_frontend` then holds each kit to its own list.
COLOR_SCHEME_CHOICES: tuple[str, ...] = tuple(
    dict.fromkeys(scheme for kit in UI_KITS.values() for scheme in kit.color_schemes)
)


def get_ui_kit(name: str) -> UiKit | None:
    """Return the registered kit called ``name``, or ``None``."""
    return UI_KITS.get(name)


def available_ui_kits(framework: object) -> tuple[str, ...]:
    """Return the selectable kits for ``framework``, the default first."""
    names = [kit.name for kit in UI_KITS.values() if kit.supported and framework in kit.frameworks]
    return tuple(sorted(names, key=lambda n: (n != DEFAULT_UI_KIT, n)))


def applies_to(framework: object) -> bool:
    """True when any registered kit targets ``framework``."""
    return any(framework in kit.frameworks for kit in UI_KITS.values())


def nuxt_ui_primary(color_scheme: str) -> str:
    """Map a ``default_color_scheme`` value to a Nuxt UI ``primary`` palette."""
    return _PALETTE_FOR_SCHEME.get(color_scheme, color_scheme)


def check_kit(frontend: FrontendConfig) -> UiKit | None:
    """Refuse a ``ui_kit`` value forge cannot render, whatever the other settings.

    Returns the kit when the framework has kits, ``None`` when it has none.
    Raises ``ValueError`` for a value that is not a name, a kit set on a
    template without kits, and an unknown or unselectable kit.
    """
    framework = frontend.framework
    name = frontend.ui_kit
    if not isinstance(name, str):
        raise ValueError(f"ui_kit must be a UI kit name, got {type(name).__name__}: {name!r}")
    if not applies_to(framework):
        if name != DEFAULT_UI_KIT:
            raise ValueError(
                f"ui_kit '{name}' applies to the Vue template only; "
                f"{framework.value} keeps its own UI kit."
            )
        return None
    kit = get_ui_kit(name)
    if kit is None or not kit.supported or framework not in kit.frameworks:
        choices = ", ".join(available_ui_kits(framework))
        raise ValueError(
            f"UI kit '{name}' is not available for {framework.value}. Choose from: {choices}"
        )
    return kit


def check_frontend(frontend: FrontendConfig) -> None:
    """Refuse a kit the frontend cannot render with its other settings.

    Raises ``ValueError`` (the error type ``FrontendConfig.validate`` uses) for
    everything :func:`check_kit` refuses, a layout that does not list the kit,
    chat on a kit without the chat UI, and a colour scheme the kit does not
    accept. A template without kits (Svelte, Flutter) accepts the eight
    :data:`COLOR_SCHEMES`. The generator calls this too, so a programmatic
    config that skipped ``validate()`` cannot render one kit while recording
    another, or render two variants of one file.
    """
    kit = check_kit(frontend)
    if kit is None:
        if (
            frontend.framework in (FrontendFramework.SVELTE, FrontendFramework.FLUTTER)
            and frontend.default_color_scheme not in COLOR_SCHEMES
        ):
            raise ValueError(
                f"Colour scheme '{frontend.default_color_scheme}' is not available for "
                f"{frontend.framework.value}. Choose from: {', '.join(COLOR_SCHEMES)}"
            )
        return
    framework = frontend.framework
    name = kit.name

    # Local import: forge.layout_variants imports forge.config, which imports
    # this module lazily from FrontendConfig.validate.
    from forge.layout_variants import available_layouts, get_layout_variant  # noqa: PLC0415

    variant = get_layout_variant(framework, frontend.layout)
    if variant is not None and name not in variant.ui_kits:
        with_kit = [
            layout
            for layout in available_layouts(framework)
            if name in getattr(get_layout_variant(framework, layout), "ui_kits", ())
        ]
        raise ValueError(
            f"Layout '{frontend.layout}' does not support UI kit '{name}' yet. "
            f"Layouts with {name}: {', '.join(with_kit) or '(none)'}"
        )
    if frontend.include_chat and not kit.chat:
        raise ValueError(
            f"UI kit '{name}' does not support include_chat yet: the AI chat UI is "
            f"built on shadcn-vue. Turn include_chat off or use ui_kit '{DEFAULT_UI_KIT}'."
        )
    if frontend.default_color_scheme not in kit.color_schemes:
        raise ValueError(
            f"Colour scheme '{frontend.default_color_scheme}' is not available with UI kit "
            f"'{name}'. Choose from: {', '.join(kit.color_schemes)}"
        )


def refusal(
    kind: str,
    name: str,
    kits: Iterable[str],
    project_kit: str,
    *,
    via: str | None = None,
    replaced_by: Mapping[str, str] | None = None,
) -> str:
    """Format one kit refusal for a component or fragment.

    ``via`` names the selected component that pulls a refused child in;
    ``replaced_by`` maps a kit to the component a project uses instead.
    """
    supported = ", ".join(kits) or "(none)"
    subject = f"{kind} '{via}' needs '{name}', which" if via else f"{kind} '{name}'"
    message = f"{subject} renders only with UI kit {supported}; the project uses '{project_kit}'."
    replacement = (replaced_by or {}).get(project_kit)
    if replacement:
        message += f" Use {replacement} instead."
    return message
