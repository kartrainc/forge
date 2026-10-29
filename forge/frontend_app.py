"""Frontend application templates: what a Vue app contains beyond its kit (ADR-015).

``frontend.app_template`` chooses the application's shape, as
``BackendConfig.app_template`` does for a service. ``crud-app`` is the
template's historical output and the default: a sample application with a
CRUD slice per backend feature, home, profile, settings and sign-in pages and
a hard-coded sidebar. ``app-shell`` renders the boilerplate and a configurable
shell, and no page of its own: the project declares its ``layouts`` (each with
a ``shell`` and its menus), its ``routing`` contracts, its ``auth`` settings and
the ``extras`` it wants, and composes its own routes into the router factory
the template generates.

This module holds the registries and the validation; the values reach the
template as Copier answers built by :func:`template_answers`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from forge.config._frontend import FrontendFramework

if TYPE_CHECKING:
    from forge.config import FrontendConfig

DEFAULT_APP_TEMPLATE = "crud-app"
APP_SHELL = "app-shell"


@dataclass(frozen=True)
class AppTemplate:
    """One selectable application template of the Vue frontend."""

    name: str
    display_label: str
    frameworks: tuple[FrontendFramework, ...]
    #: The UI kits the template renders with; empty means every kit.
    ui_kits: tuple[str, ...] = ()


APP_TEMPLATES: dict[str, AppTemplate] = {
    DEFAULT_APP_TEMPLATE: AppTemplate(
        name=DEFAULT_APP_TEMPLATE,
        display_label="Sample application with CRUD pages",
        frameworks=(FrontendFramework.VUE, FrontendFramework.SVELTE, FrontendFramework.FLUTTER),
    ),
    APP_SHELL: AppTemplate(
        name=APP_SHELL,
        display_label="Boilerplate and a configurable shell, no pages",
        frameworks=(FrontendFramework.VUE,),
        ui_kits=("nuxt-ui",),
    ),
}


@dataclass(frozen=True)
class Shell:
    """One application shell a layout can use."""

    name: str
    display_label: str
    ui_kits: tuple[str, ...]


SHELLS: dict[str, Shell] = {
    "header-sidebar": Shell(
        name="header-sidebar",
        display_label="Full-width header above a collapsible sidebar, a page and a right panel",
        ui_kits=("nuxt-ui",),
    ),
}
DEFAULT_SHELL = "header-sidebar"

#: The destinations the guards use, in the order they are documented.
ROUTING_KEYS: tuple[str, ...] = ("landing", "sign_in", "not_found", "auth_stuck", "forbidden")
BUILTIN = "builtin"
#: The route each built-in system page is registered under.
BUILTIN_ROUTE_NAMES: dict[str, str] = {
    "sign_in": "login",
    "not_found": "not-found",
    "auth_stuck": "auth-stuck",
    "forbidden": "forbidden",
}
#: Destinations that only exist with authentication on.
_AUTH_DESTINATIONS = frozenset({"sign_in", "auth_stuck"})

AUTH_RUNTIMES: tuple[str, ...] = ("forge", "project")
SESSION_DIALOGS: tuple[str, ...] = ("builtin", "project")

#: What the sample application offers beyond the boilerplate; ``crud-app`` has all.
EXTRAS: tuple[str, ...] = (
    "home",
    "profile",
    "settings",
    "health",
    "confirm",
    "list_helpers",
    "forms",
    "capabilities",
    "event_stream",
    "layout_helpers",
    "msw",
)
#: Extras whose code imports forge's ``useAuth``.
_NEEDS_FORGE_AUTH = frozenset({"capabilities"})
#: Extras that are pages, registered by the router factory under the first layout.
PAGE_EXTRAS: tuple[str, ...] = ("home", "profile", "settings")

MENUS: tuple[str, ...] = ("primary", "account", "footer")

#: The frontend fragments of forge's authentication runtime, which an app with
#: ``auth.runtime: project`` does not receive.
FORGE_AUTH_RUNTIME_FRAGMENTS: frozenset[str] = frozenset({"platform_auth_session_timeout_vue"})

_KEBAB = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_ROUTE_NAME = re.compile(r"^[a-z][a-z0-9-]*$")
_ICON = re.compile(r"^i-[a-z0-9]+(?:-[a-z0-9]+)+$")
_BADGE = re.compile(r"^[a-z][a-z0-9_-]*$")
_LAYOUT_KEYS = frozenset({"shell", "placeholder", "menus"})
_SECTION_KEYS = frozenset({"label", "items"})
_ITEM_KEYS = frozenset({"id", "label", "icon", "to", "action", "children", "badge"})
_DESTINATIONS = ("to", "action", "children")


def is_app_shell(fc: FrontendConfig) -> bool:
    return fc.app_template == APP_SHELL


def check_application(fc: FrontendConfig) -> None:
    """Refuse an application template, layout, menu, routing or auth value forge cannot render.

    Raises ``ValueError``, the error type ``FrontendConfig.validate`` uses.
    """
    name = fc.app_template
    if not isinstance(name, str) or name not in APP_TEMPLATES:
        raise ValueError(f"app_template must be one of {', '.join(APP_TEMPLATES)}, got {name!r}.")
    configured = [
        key for key in ("layouts", "routing", "auth", "extras") if getattr(fc, key) is not None
    ]
    if name == DEFAULT_APP_TEMPLATE:
        if configured:
            raise ValueError(
                f"{', '.join(configured)} need app_template '{APP_SHELL}'; "
                f"app_template '{DEFAULT_APP_TEMPLATE}' renders its own sample pages and sidebar."
            )
        return
    template = APP_TEMPLATES[name]
    if fc.framework not in template.frameworks:
        raise ValueError(
            f"app_template '{name}' is available for the Vue template only, not "
            f"{fc.framework.value}."
        )
    if template.ui_kits and fc.ui_kit not in template.ui_kits:
        raise ValueError(
            f"app_template '{name}' renders with ui_kit {', '.join(template.ui_kits)}, "
            f"not '{fc.ui_kit}'."
        )
    if fc.features:
        raise ValueError(
            f"app_template '{name}' renders no CRUD pages; drop features {fc.features} "
            f"or use app_template '{DEFAULT_APP_TEMPLATE}'."
        )
    from forge.layout_variants import DEFAULT_LAYOUT  # noqa: PLC0415

    if fc.layout != DEFAULT_LAYOUT:
        raise ValueError(
            f"app_template '{name}' takes its shells from layouts; layout '{fc.layout}' "
            f"belongs to app_template '{DEFAULT_APP_TEMPLATE}'."
        )
    _check_layouts(fc)
    _check_routing(fc)
    _check_auth(fc)
    _check_extras(fc)


def _require_mapping(value: object, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{where} must be a table, got {type(value).__name__}.")
    return value


def _unknown(keys: Iterable[object], allowed: frozenset[str], where: str) -> None:
    extra = sorted(str(k) for k in keys if k not in allowed)
    if extra:
        raise ValueError(
            f"{where} has unknown keys {extra}; allowed: {', '.join(sorted(allowed))}."
        )


def layout_entries(fc: FrontendConfig) -> list[tuple[str, Mapping[str, Any]]]:
    """The layouts in their declared order, as ``(name, layout)``; the first is mounted at ``/``."""
    raw = fc.layouts or []
    if isinstance(raw, Mapping):
        return [(str(name), value) for name, value in raw.items()]
    if not isinstance(raw, list):
        raise ValueError(f"layouts must be a table of named layouts, got {type(raw).__name__}.")
    entries: list[tuple[str, Mapping[str, Any]]] = []
    for i, item in enumerate(raw):
        entry = _require_mapping(item, f"layouts[{i}]")
        name = entry.get("name")
        if not isinstance(name, str):
            raise ValueError(f"layouts[{i}] needs a name, got {name!r}.")
        entries.append((name, {k: v for k, v in entry.items() if k != "name"}))
    return entries


def _check_layouts(fc: FrontendConfig) -> None:
    entries = layout_entries(fc)
    if not entries:
        raise ValueError(f"app_template '{APP_SHELL}' needs at least one entry in layouts.")
    names = [name for name, _ in entries]
    if len(set(names)) != len(names):
        raise ValueError("layouts names a layout twice.")
    for layout_name, raw in entries:
        where = f"layouts.{layout_name}"
        if not isinstance(layout_name, str) or not _KEBAB.match(layout_name):
            raise ValueError(f"Layout name {layout_name!r} must be kebab-case.")
        layout = _require_mapping(raw, where)
        _unknown(layout.keys(), _LAYOUT_KEYS, where)
        shell_name = layout.get("shell", DEFAULT_SHELL)
        shell = SHELLS.get(shell_name) if isinstance(shell_name, str) else None
        if shell is None:
            raise ValueError(
                f"{where}.shell must be one of {', '.join(SHELLS)}, got {shell_name!r}."
            )
        if fc.ui_kit not in shell.ui_kits:
            raise ValueError(
                f"{where}.shell '{shell.name}' renders with ui_kit {', '.join(shell.ui_kits)}."
            )
        placeholder = layout.get("placeholder")
        if placeholder is not None and not _is_route_name(placeholder):
            raise ValueError(f"{where}.placeholder must be a route name, got {placeholder!r}.")
        menus = _require_mapping(layout.get("menus") or {}, f"{where}.menus")
        _unknown(menus.keys(), frozenset(MENUS), f"{where}.menus")
        ids: set[str] = set()
        for menu_name, sections in menus.items():
            _check_sections(sections, f"{where}.menus.{menu_name}", ids)


def _check_sections(sections: object, where: str, ids: set[str]) -> None:
    if not isinstance(sections, list):
        raise ValueError(f"{where} must be a list of sections, got {type(sections).__name__}.")
    for i, raw in enumerate(sections):
        section = _require_mapping(raw, f"{where}[{i}]")
        _unknown(section.keys(), _SECTION_KEYS, f"{where}[{i}]")
        label = section.get("label")
        if label is not None and (not isinstance(label, str) or not label.strip()):
            raise ValueError(f"{where}[{i}].label must be a non-empty string.")
        items = section.get("items")
        if not isinstance(items, list) or not items:
            raise ValueError(f"{where}[{i}].items must be a non-empty list.")
        for j, item in enumerate(items):
            _check_item(item, f"{where}[{i}].items[{j}]", ids, nested=False)


def _check_item(raw: object, where: str, ids: set[str], *, nested: bool) -> None:
    item = _require_mapping(raw, where)
    _unknown(item.keys(), _ITEM_KEYS, where)
    item_id = item.get("id")
    if not isinstance(item_id, str) or not _KEBAB.match(item_id):
        raise ValueError(f"{where}.id must be kebab-case, got {item_id!r}.")
    if item_id in ids:
        raise ValueError(f"{where}.id '{item_id}' is used twice in the layout.")
    ids.add(item_id)
    label = item.get("label")
    if not isinstance(label, str) or not label.strip():
        raise ValueError(f"{where}.label must be a non-empty string.")
    icon = item.get("icon")
    if icon is not None and (not isinstance(icon, str) or not _ICON.match(icon)):
        raise ValueError(f"{where}.icon must be an Iconify name like i-lucide-house, got {icon!r}.")
    badge = item.get("badge")
    if badge is not None and (not isinstance(badge, str) or not _BADGE.match(badge)):
        raise ValueError(f"{where}.badge must be a key like 'unread', got {badge!r}.")
    given = [key for key in _DESTINATIONS if item.get(key) is not None]
    if len(given) > 1:
        raise ValueError(f"{where} sets {' and '.join(given)}; an item has at most one of them.")
    to = item.get("to")
    if to is not None and not _is_route_name(to):
        raise ValueError(f"{where}.to must be a route name, got {to!r}.")
    action = item.get("action")
    if action is not None and (not isinstance(action, str) or not _KEBAB.match(action)):
        raise ValueError(f"{where}.action must be a kebab-case action id, got {action!r}.")
    children = item.get("children")
    if children is not None:
        if nested:
            raise ValueError(f"{where} nests children; sub-menus are one level deep.")
        if not isinstance(children, list) or not children:
            raise ValueError(f"{where}.children must be a non-empty list.")
        for k, child in enumerate(children):
            _check_item(child, f"{where}.children[{k}]", ids, nested=True)


def _is_route_name(value: object) -> bool:
    return isinstance(value, str) and bool(_ROUTE_NAME.match(value))


def _check_routing(fc: FrontendConfig) -> None:
    routing = _require_mapping(fc.routing or {}, "routing")
    _unknown(routing.keys(), frozenset(ROUTING_KEYS), "routing")
    landing = routing.get("landing")
    if not _is_route_name(landing) or landing == BUILTIN:
        raise ValueError(
            "routing.landing must name the route a signed-in visit of '/' opens "
            f"(a project route or an extra page such as 'home'), got {landing!r}."
        )
    for key in ROUTING_KEYS[1:]:
        value = routing.get(key, BUILTIN)
        if value != BUILTIN and not _is_route_name(value):
            raise ValueError(f"routing.{key} must be 'builtin' or a route name, got {value!r}.")
        if key in _AUTH_DESTINATIONS and value != BUILTIN and not fc.include_auth:
            raise ValueError(f"routing.{key} needs include_auth.")


def _check_auth(fc: FrontendConfig) -> None:
    auth = _require_mapping(fc.auth or {}, "auth")
    _unknown(auth.keys(), frozenset({"runtime", "session_dialog"}), "auth")
    runtime = auth.get("runtime", "forge")
    if runtime not in AUTH_RUNTIMES:
        raise ValueError(
            f"auth.runtime must be one of {', '.join(AUTH_RUNTIMES)}, got {runtime!r}."
        )
    dialog = auth.get("session_dialog")
    if dialog is not None:
        if dialog not in SESSION_DIALOGS:
            raise ValueError(
                f"auth.session_dialog must be one of {', '.join(SESSION_DIALOGS)}, got {dialog!r}."
            )
        if not fc.include_auth:
            raise ValueError("auth.session_dialog needs include_auth.")
        if dialog == BUILTIN and runtime != "forge":
            raise ValueError(
                "auth.session_dialog 'builtin' is forge's dialog over forge's session "
                "runtime; with auth.runtime 'project' the dialog is the project's."
            )


def _check_extras(fc: FrontendConfig) -> None:
    extras = fc.extras or []
    if not isinstance(extras, list) or not all(isinstance(e, str) for e in extras):
        raise ValueError("extras must be a list of names.")
    unknown = sorted(set(extras) - set(EXTRAS))
    if unknown:
        raise ValueError(f"Unknown extras {unknown}; choose from {', '.join(EXTRAS)}.")
    if len(set(extras)) != len(extras):
        raise ValueError("extras names an entry twice.")
    if auth_runtime(fc) != "forge":
        needing = sorted(_NEEDS_FORGE_AUTH & set(extras))
        if needing:
            raise ValueError(
                f"extras {needing} use forge's useAuth; they need auth.runtime 'forge'."
            )


def auth_runtime(fc: FrontendConfig) -> str:
    """``forge`` or ``project``; always ``forge`` for ``crud-app``."""
    if not is_app_shell(fc):
        return "forge"
    return str((fc.auth or {}).get("runtime", "forge"))


def session_dialog(fc: FrontendConfig) -> str:
    """``builtin``, ``project`` or ``none`` (no authentication)."""
    if not fc.include_auth:
        return "none"
    if not is_app_shell(fc):
        return BUILTIN
    default = BUILTIN if auth_runtime(fc) == "forge" else "project"
    return str((fc.auth or {}).get("session_dialog", default))


def resolved_extras(fc: FrontendConfig) -> tuple[str, ...]:
    """The extras an app renders: all of them for ``crud-app``."""
    if not is_app_shell(fc):
        return EXTRAS
    return tuple(e for e in EXTRAS if e in (fc.extras or []))


def resolved_routing(fc: FrontendConfig) -> dict[str, str]:
    """Every routing destination, ``builtin`` where the configuration is silent."""
    routing = dict(fc.routing or {})
    return {key: str(routing.get(key, BUILTIN)) for key in ROUTING_KEYS}


def builtin_pages(fc: FrontendConfig) -> tuple[str, ...]:
    """The system pages forge emits for an ``app-shell`` app."""
    if not is_app_shell(fc):
        return ()
    routing = resolved_routing(fc)
    pages = []
    for key in ROUTING_KEYS[1:]:
        if routing[key] != BUILTIN:
            continue
        if key in _AUTH_DESTINATIONS and not fc.include_auth:
            continue
        pages.append(key)
    return tuple(pages)


def application_config(fc: FrontendConfig) -> dict[str, Any]:
    """The resolved application the template renders into ``src/shared/application/config.ts``."""
    routing = resolved_routing(fc)
    destinations = {
        key: (BUILTIN_ROUTE_NAMES[key] if value == BUILTIN else value)
        for key, value in routing.items()
        if key == "landing" or key in builtin_pages(fc) or value != BUILTIN
    }
    layouts: dict[str, Any] = {}
    for name, raw in layout_entries(fc):
        layout = dict(raw)
        menus = layout.get("menus") or {}
        layouts[name] = {
            "shell": layout.get("shell", DEFAULT_SHELL),
            "placeholder": layout.get("placeholder"),
            "menus": {menu: [_section(s) for s in menus.get(menu, [])] for menu in MENUS},
        }
    return {
        "title": fc.project_name,
        "auth": fc.include_auth,
        "routing": destinations,
        "builtin": list(builtin_pages(fc)),
        "layouts": layouts,
        "extras": [e for e in resolved_extras(fc) if e in PAGE_EXTRAS],
    }


_ITEM_ORDER = ("id", "label", "icon", "to", "action", "badge", "children")


def _item(raw: Mapping[str, Any]) -> dict[str, Any]:
    """A menu item with its keys in one order, whatever order the configuration (or the
    recipe, written with sorted keys) gave them, so a regeneration renders the same file."""
    item = {key: raw[key] for key in _ITEM_ORDER if raw.get(key) is not None}
    if "children" in item:
        item["children"] = [_item(child) for child in item["children"]]
    return item


def _section(raw: Mapping[str, Any]) -> dict[str, Any]:
    section: dict[str, Any] = {"label": raw["label"]} if raw.get("label") else {}
    section["items"] = [_item(item) for item in raw.get("items", [])]
    return section


def template_answers(fc: FrontendConfig, *, session_timeout: bool) -> dict[str, Any]:
    """The Copier answers of :mod:`forge.variable_mapper` for the application template.

    ``session_timeout`` says whether the session-timeout fragment reaches the app
    (``auth.mode`` generate and a forge auth runtime): the built-in dialog needs it.
    """
    shell = is_app_shell(fc)
    return {
        "app_template": fc.app_template,
        "extras": ",".join(resolved_extras(fc)),
        "auth_runtime": auth_runtime(fc),
        "session_dialog": (
            session_dialog(fc)
            if session_dialog(fc) != BUILTIN or session_timeout or not shell
            else "project"
        ),
        "builtin_pages": ",".join(builtin_pages(fc)),
        "application": (
            json.dumps(application_config(fc), indent=2, ensure_ascii=False) if shell else "{}"
        ),
    }
