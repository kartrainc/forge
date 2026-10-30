"""Backend language registry — built-in enum + plugin sentinels + per-language spec.

Each :class:`BackendSpec` registered in :data:`BACKEND_REGISTRY` ties a
backend language to its Copier template, its post-generate
:class:`~forge.toolchains.BackendToolchain`, and its CLI prompt
metadata. Plugins extend the registry via
:func:`~forge.api.ForgeAPI.add_backend`, which delegates to
:func:`register_backend_language` here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from forge.config._validators import validate_features, validate_port


class BackendLanguage(Enum):
    PYTHON = "python"
    NODE = "node"
    RUST = "rust"
    GO = "go"


# Plugin-registered backend language values. Keyed by the wire value
# (``"go"``, ``"java"``), value is a sentinel object (_PluginLanguage)
# that mimics a BackendLanguage member well enough for the generator
# dispatch + fragment lookup. Look up via ``resolve_backend_language``.
PLUGIN_LANGUAGES: dict[str, _PluginLanguage] = {}


class _PluginLanguage:
    """Synthetic BackendLanguage member for plugin-registered languages.

    Behaves like a frozen enum member: has ``.value``, ``.name``, and
    hashes consistently so it works as a dict key in BACKEND_REGISTRY.
    The Python enum machinery refuses to return non-Enum values from
    ``_missing_``, so we expose a separate resolution function instead
    of pretending this is a real member.
    """

    __slots__ = ("value", "name")

    def __init__(self, value: str) -> None:
        self.value = value
        self.name = value.upper()

    def __repr__(self) -> str:
        return f"<BackendLanguage.{self.name} (plugin)>"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, _PluginLanguage):
            return self.value == other.value
        if isinstance(other, BackendLanguage):
            return self.value == other.value
        return NotImplemented

    def __hash__(self) -> int:
        return hash(("BackendLanguage", self.value))


def register_backend_language(value: str) -> _PluginLanguage:
    """Register a plugin language value. Returns the sentinel member."""
    if value not in PLUGIN_LANGUAGES:
        PLUGIN_LANGUAGES[value] = _PluginLanguage(value)
    return PLUGIN_LANGUAGES[value]


def resolve_backend_language(value: str) -> BackendLanguage | _PluginLanguage:
    """Look up a language by string value. Checks built-in enum first,
    then plugin-registered sentinels. Raises ValueError if neither
    matches — callers treat that as "unknown language".

    Used by fragment/generator code that deals with language strings
    from YAML configs or plugin metadata.
    """
    for member in BackendLanguage:
        if member.value == value:
            return member
    if value in PLUGIN_LANGUAGES:
        return PLUGIN_LANGUAGES[value]
    raise ValueError(f"Unknown backend language: {value!r}")


def available_backend_languages() -> list[str]:
    """Wire values of every backend language forge can generate right now —
    the built-ins plus any a plugin has registered via
    :meth:`forge.api.ForgeAPI.add_backend`.

    Read at call time (not import time) so the CLI's ``--backend-language``
    choices reflect plugins loaded earlier in ``main()``. Built-ins come
    first in declaration order; plugin languages follow, sorted.
    """
    return [member.value for member in BackendLanguage] + sorted(PLUGIN_LANGUAGES)


def _default_toolchain_factory() -> Any:
    """Lazy import to break the config → toolchains cycle.

    ``forge.toolchains`` depends only on ``forge.errors``, not ``forge.config``,
    so this factory runs at ``BackendSpec(...)`` instantiation time (well
    after import) without creating an import cycle.
    """
    from forge.toolchains import NOOP_TOOLCHAIN  # noqa: PLC0415

    return NOOP_TOOLCHAIN


@dataclass(frozen=True)
class BackendSpec:
    """Static metadata for a backend language: template, prompts, version field.

    Adding a 4th backend means adding one entry here plus one Copier template
    directory under `forge/templates/services/`. The CLI prompt loop, generator
    dispatch, and variable-mapper context builder all read from this registry.

    ``toolchain`` carries the per-language install / verify / post-generate
    hooks previously hardcoded in ``generator.py``'s language dispatch.
    Plugin-registered backends attach their own implementation — see
    ``forge.toolchains.BackendToolchain`` Protocol. Typed as ``Any`` to keep
    ``forge.config`` free of the ``forge.toolchains`` import (the factory
    resolves it lazily).

    ``version`` is the template's own semver — bumped when the base
    template's emitted shape changes in a way that warrants a Copier
    re-render on ``forge --update``. Resolution at generate time prefers
    ``_forge_template.toml``'s ``[template].version`` when present, falling
    back to this field (so the spec carries a typed default and individual
    templates can drift independently of spec edits).
    """

    template_dir: str  # path under forge/templates/, e.g. "services/python-service-template"
    display_label: str  # shown in CLI prompts and log messages
    version_field: str  # name of the BackendConfig attribute holding the version
    version_choices: tuple[str, ...]  # interactive prompt choices, first is default
    toolchain: Any = field(default_factory=_default_toolchain_factory)
    version: str = "1.0.0"  # template semver; see :mod:`forge.sync.template_version`


def _python_toolchain_factory() -> Any:
    from forge.toolchains.python import PYTHON_TOOLCHAIN  # noqa: PLC0415

    return PYTHON_TOOLCHAIN


def _node_toolchain_factory() -> Any:
    from forge.toolchains.node import NODE_TOOLCHAIN  # noqa: PLC0415

    return NODE_TOOLCHAIN


def _rust_toolchain_factory() -> Any:
    from forge.toolchains.rust import RUST_TOOLCHAIN  # noqa: PLC0415

    return RUST_TOOLCHAIN


def _go_toolchain_factory() -> Any:
    from forge.toolchains.go import GO_TOOLCHAIN  # noqa: PLC0415

    return GO_TOOLCHAIN


# BACKEND_REGISTRY keys are either real ``BackendLanguage`` members or
# ``_PluginLanguage`` sentinels — both share the ``.value`` attribute so
# downstream code can treat them uniformly.
BACKEND_REGISTRY: dict[BackendLanguage | _PluginLanguage, BackendSpec] = {
    BackendLanguage.PYTHON: BackendSpec(
        template_dir="services/python-service-template",
        display_label="Python (FastAPI)",
        version_field="python_version",
        version_choices=("3.13", "3.12", "3.11"),
        toolchain=_python_toolchain_factory(),
        version="1.0.0",
    ),
    BackendLanguage.NODE: BackendSpec(
        template_dir="services/node-service-template",
        display_label="Node.js (Fastify)",
        version_field="node_version",
        # The template's toolchain (vitest 5) runs on ^22.12, ^24 and >=26.
        version_choices=("26", "24", "22"),
        toolchain=_node_toolchain_factory(),
        version="1.0.0",
    ),
    BackendLanguage.RUST: BackendSpec(
        template_dir="services/rust-service-template",
        display_label="Rust (Axum)",
        version_field="rust_edition",
        version_choices=("2024", "2021"),
        toolchain=_rust_toolchain_factory(),
        version="1.0.0",
    ),
    BackendLanguage.GO: BackendSpec(
        template_dir="services/go-service-template",
        display_label="Go (chi)",
        version_field="go_version",
        version_choices=("1.27", "1.26"),
        toolchain=_go_toolchain_factory(),
        version="1.0.0",
    ),
}

# A Go service's module path is its name, so a name equal to a top-level
# standard-library package ("log", "net", ...) makes every import ambiguous.
GO_RESERVED_MODULE_NAMES: frozenset[str] = frozenset(
    {
        "archive",
        "bufio",
        "builtin",
        "bytes",
        "cmd",
        "cmp",
        "compress",
        "container",
        "context",
        "crypto",
        "database",
        "debug",
        "embed",
        "encoding",
        "errors",
        "expvar",
        "flag",
        "fmt",
        "go",
        "hash",
        "html",
        "image",
        "index",
        "internal",
        "io",
        "iter",
        "log",
        "maps",
        "math",
        "mime",
        "net",
        "os",
        "path",
        "plugin",
        "reflect",
        "regexp",
        "runtime",
        "slices",
        "sort",
        "strconv",
        "strings",
        "structs",
        "sync",
        "syscall",
        "testing",
        "text",
        "time",
        "unicode",
        "unique",
        "unsafe",
        "uuid",
        "vendor",
        "weak",
    }
)


class _DefaultFeatures(list):
    """The features of a backend whose configuration names none, told apart from an explicit list."""


def _default_features() -> list[str]:
    return _DefaultFeatures(["items"])


@dataclass
class BackendConfig:
    """Backend service configuration."""

    name: str = "backend"
    project_name: str = ""
    language: BackendLanguage = BackendLanguage.PYTHON
    description: str = "A microservice"
    # Omitted: ["items"], or none for an app template that owns no database (resolved in __post_init__).
    features: list[str] = field(default_factory=_default_features)
    python_version: str = "3.13"
    node_version: str = "26"
    rust_edition: str = "2024"
    go_version: str = "1.27"
    server_port: int = 5000
    # Application-template variant — the backend analogue of
    # FrontendConfig.layout. Selects the Copier service shape via
    # forge.backend_app_templates; `crud-service` (default) IS today's
    # baseline per-language template, so leaving it untouched reproduces
    # pre-app-template output byte-for-byte (the golden gate).
    app_template: str = "crud-service"
    # Python-only Copier prompt — `monorepo` (default), `standalone`,
    # or `none`. Threaded into ``variable_mapper.backend_context`` only
    # when non-None, so the copier.yml default (`monorepo`) still wins
    # when the caller doesn't care. Forge's own CI sets ``none`` because
    # there's no sibling ``sdks/`` tree to mount; production users in
    # the platform monorepo leave it at the default.
    sdk_consumption: str | None = None
    # Phase 4 (platform synthesis): names of OTHER backends in this project that
    # this backend makes service-to-service calls to. Drives the synthesized S2S
    # client registry + inter-service URL injection when auth.service_discovery
    # is on. Empty (default) = no declared inter-service edges → synthesis is a
    # no-op for this backend, so output stays byte-identical. Graph-membership
    # validation (each name must be a real backend) lives in ProjectConfig.
    depends_on: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._resolve_default_features()

    def _resolve_default_features(self) -> None:
        # Omitted features stay marked as the default, so a template set later
        # (dataclasses.replace, an assignment) gets its own default too.
        if isinstance(self.features, _DefaultFeatures):
            self.features = _DefaultFeatures(["items"] if self._uses_database() else [])

    def _uses_database(self) -> bool:
        # Local import: forge.backend_app_templates imports BackendLanguage from this package.
        from forge.backend_app_templates import backend_uses_database  # noqa: PLC0415

        return backend_uses_database(self.language, self.app_template)

    def validate(self) -> None:
        self._resolve_default_features()
        validate_port(self.server_port, f"Backend '{self.name}' port")
        if not re.match(r"^[a-z][a-z0-9_-]*$", self.name):
            raise ValueError(f"Backend name '{self.name}' must be lowercase kebab/snake case.")
        if self.language == BackendLanguage.GO and self.name in GO_RESERVED_MODULE_NAMES:
            raise ValueError(
                f"Go backend name '{self.name}' is a Go standard-library package; "
                "it would make the service's module imports ambiguous. Choose another name."
            )
        for dep in self.depends_on:
            if not re.match(r"^[a-z][a-z0-9_-]*$", dep):
                raise ValueError(
                    f"Backend '{self.name}' depends_on entry '{dep}' must be a "
                    "lowercase kebab/snake service name."
                )
            if dep == self.name:
                raise ValueError(f"Backend '{self.name}' cannot depend on itself.")
        if self.features:
            validate_features(self.features)
        # A CRUD feature is a table, its API and the frontend's pages for it: a template without a
        # database would publish pages for an API it does not serve.
        if self.features and not self._uses_database():
            raise ValueError(
                f"Backend '{self.name}' uses the '{self.app_template}' app template, which owns no "
                "database, so it serves no CRUD features; leave `features` out."
            )
        # The chosen application template must be a registered variant for
        # this language. Local import avoids a config<->backend_app_templates
        # import cycle (the latter imports BackendLanguage from this package).
        from forge.backend_app_templates import (  # noqa: PLC0415
            available_backend_templates,
            get_backend_application_template,
        )

        if get_backend_application_template(self.language, self.app_template) is None:
            avail = available_backend_templates(self.language)
            raise ValueError(
                f"Application template '{self.app_template}' is not available for "
                f"{self.language.value}. "
                f"Choose from: {', '.join(avail) if avail else '(none registered)'}"
            )
