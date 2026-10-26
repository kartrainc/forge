"""Tests for Pillar E.1 — the ``error_port`` fragment + option.

Promotes RFC-007's accepted error contract (``docs/rfcs/RFC-007-error-contract.md``)
from base-template-woven code into a swappable port. The port surface is
one ``serialize(exc) -> envelope`` method that returns the canonical
RFC-007 envelope::

    {
      "error": {
        "code": "...",            # RFC-007 enum, machine-readable
        "message": "...",         # human-readable, UI-safe
        "type": "...",            # concrete class name
        "context": {...},         # freeform structured data
        "correlation_id": "..."   # request id (header-driven)
      }
    }

The fragment ships tier-1 from the start (Python + Node + Rust) — the
wire shape is already proven cross-language by the auth SDKs, so a
Python-only port would be a downgrade. Plugins shipping custom envelopes
implement the ``ErrorPort`` Protocol / interface / trait and register
their adapter in place of ``DefaultErrorPort``.

The companion option ``observability.error_envelope`` (BOOL, default
``True``) enables the fragment. Default ``True`` preserves existing
behaviour — flipping it to ``False`` will eventually strip the
base-template error code via the existing strip mechanism (follow-up;
see plan).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from forge.capability_resolver import resolve
from forge.config import BackendConfig, BackendLanguage, ProjectConfig
from forge.fragments import FRAGMENT_REGISTRY
from forge.options import OPTION_REGISTRY

# -- fragment-registry shape --------------------------------------------------


def test_error_port_fragment_registered() -> None:
    """The fragment is reachable by name from the global registry."""
    assert "error_port" in FRAGMENT_REGISTRY


def test_error_port_covers_all_three_built_ins() -> None:
    """Tier-1 from the start — RFC-007's wire shape works cross-language
    (the auth SDKs prove it), so Python-only would be a downgrade."""
    frag = FRAGMENT_REGISTRY["error_port"]
    assert BackendLanguage.PYTHON in frag.implementations
    assert BackendLanguage.NODE in frag.implementations
    assert BackendLanguage.RUST in frag.implementations
    assert frag.parity_tier == 1


def test_error_port_has_no_capabilities_or_dependencies() -> None:
    """The port is base infrastructure — no Redis/Postgres/Keycloak
    requirement, no other-fragment dependencies. Plugins shipping
    custom envelopes drop the default adapter and supply their own."""
    frag = FRAGMENT_REGISTRY["error_port"]
    assert frag.capabilities == ()
    assert frag.depends_on == ()


def test_error_port_rust_declares_serialisation_deps() -> None:
    """The rust port + default adapter use serde + serde_json +
    thiserror in their type declarations and the ``DefaultErrorPort``
    body. Listed on the port impl so a project that ships only
    ``error_port`` (no custom adapter wired) still compiles. Cargo
    de-dupes when other fragments already pull these in."""
    frag = FRAGMENT_REGISTRY["error_port"]
    impl = frag.implementations[BackendLanguage.RUST]
    deps_str = " ".join(impl.dependencies)
    for needed in ("serde", "serde_json", "thiserror"):
        assert needed in deps_str, f"error_port/rust missing dep: {needed!r}"


# -- option-registry shape ----------------------------------------------------


def test_error_envelope_option_registered_with_default_true() -> None:
    """Backwards-compat guard: existing projects unchanged. Flipping
    the default to ``False`` would silently strip the port from every
    project that doesn't pin the option — explicitly assert the
    default here so a regression is caught on the next ``pytest`` run."""
    opt = OPTION_REGISTRY.get("observability.error_envelope")
    assert opt is not None, "observability.error_envelope option must be registered"
    assert opt.default is True
    # The option exists to flip the port on/off — ``enables[True]`` is
    # the fragment list the resolver pulls in.
    assert opt.enables.get(True) == ("error_port",)
    # When the user explicitly disables the option, no fragments should
    # come along for the ride.
    assert opt.enables.get(False, ()) == ()


# -- on-disk file shape -------------------------------------------------------


def _impl_root(lang: BackendLanguage) -> Path:
    return Path(FRAGMENT_REGISTRY["error_port"].implementations[lang].fragment_dir)


def test_python_port_files_land_at_conventional_paths() -> None:
    root = _impl_root(BackendLanguage.PYTHON)
    port_py = root / "files" / "src" / "app" / "ports" / "error_port.py"
    adapter_py = root / "files" / "src" / "app" / "adapters" / "error_default.py"
    assert port_py.is_file(), f"python port missing at {port_py}"
    assert adapter_py.is_file(), f"python adapter missing at {adapter_py}"


def test_node_port_files_land_at_conventional_paths() -> None:
    root = _impl_root(BackendLanguage.NODE)
    port_ts = root / "files" / "src" / "app" / "ports" / "error-port.ts"
    adapter_ts = root / "files" / "src" / "app" / "adapters" / "error-default.ts"
    assert port_ts.is_file(), f"node port missing at {port_ts}"
    assert adapter_ts.is_file(), f"node adapter missing at {adapter_ts}"


def test_rust_port_files_land_at_conventional_paths() -> None:
    """The rust port lives at its own top-level module
    (``src/error_port/{mod.rs,default.rs}``) rather than under
    ``src/ports/`` to avoid a ``mod.rs`` collision with the existing
    ``queue_port`` fragment when both happen to land in the same
    project. Same compile-time surface (`crate::error_port::ErrorPort`)
    either way."""
    root = _impl_root(BackendLanguage.RUST)
    port_rs = root / "files" / "src" / "error_port" / "mod.rs"
    adapter_rs = root / "files" / "src" / "error_port" / "default.rs"
    assert port_rs.is_file(), f"rust port missing at {port_rs}"
    assert adapter_rs.is_file(), f"rust adapter missing at {adapter_rs}"


def test_python_inject_yaml_registers_port_into_app_main() -> None:
    inject = _impl_root(BackendLanguage.PYTHON) / "inject.yaml"
    assert inject.is_file()
    entries = yaml.safe_load(inject.read_text(encoding="utf-8"))
    assert isinstance(entries, list) and len(entries) >= 1
    e = entries[0]
    # main.py is the only Python file guaranteed to exist in every
    # FastAPI backend configuration; container.py is conditional on
    # agent features being enabled. observability_otel already uses
    # the same target + marker pair (codex Phase B round 1 follow-up).
    assert e["target"] == "src/app/main.py"
    assert "APP_POST_CONFIGURE" in e["marker"]
    snippet = e["snippet"]
    # Both the port type AND the default adapter must be reachable at
    # startup — failing to import the adapter is what surfaces
    # mis-registration as a clean error at first use.
    assert "ErrorPort" in snippet
    assert "DefaultErrorPort" in snippet


def test_node_inject_yaml_registers_port_into_app() -> None:
    inject = _impl_root(BackendLanguage.NODE) / "inject.yaml"
    assert inject.is_file()
    entries = yaml.safe_load(inject.read_text(encoding="utf-8"))
    assert isinstance(entries, list) and len(entries) >= 1
    e = entries[0]
    assert e["target"] == "src/app.ts"
    assert "MIDDLEWARE_IMPORTS" in e["marker"]
    snippet = e["snippet"]
    assert "ErrorPort" in snippet
    assert "DefaultErrorPort" in snippet


def test_rust_inject_yaml_registers_module_in_lib_rs() -> None:
    inject = _impl_root(BackendLanguage.RUST) / "inject.yaml"
    assert inject.is_file()
    entries = yaml.safe_load(inject.read_text(encoding="utf-8"))
    assert isinstance(entries, list) and len(entries) >= 1
    e = entries[0]
    assert e["target"] == "src/lib.rs"
    assert "LIB_MOD_REGISTRATION" in e["marker"]
    assert "pub mod error_port" in e["snippet"]


# -- port + adapter source shape ---------------------------------------------


def _file_text(lang: BackendLanguage, *parts: str) -> str:
    return (_impl_root(lang) / "files" / Path(*parts)).read_text(encoding="utf-8")


def test_python_port_declares_serialize_protocol() -> None:
    """Port surface is one method: ``serialize(exc) -> dict``. The
    Protocol declaration is the spec the linter enforces."""
    body = _file_text(BackendLanguage.PYTHON, "src", "app", "ports", "error_port.py")
    assert "class ErrorPort(Protocol)" in body
    assert "def serialize(self, exc: Exception) -> dict" in body


def test_python_default_adapter_emits_rfc007_envelope() -> None:
    body = _file_text(BackendLanguage.PYTHON, "src", "app", "adapters", "error_default.py")
    assert "class DefaultErrorPort" in body
    # The envelope's five required fields per RFC-007 §"The envelope".
    for field in ("code", "message", "type", "context", "correlation_id"):
        assert f'"{field}"' in body, f"python adapter missing envelope field: {field!r}"
    # The top-level wrapper key must be ``error`` per RFC-007.
    assert '"error"' in body
    # Fallback path: anything that's not an ``ApplicationError`` becomes
    # an ``INTERNAL_ERROR`` with a redacted message.
    assert "INTERNAL_ERROR" in body


def test_node_port_declares_serialize_interface() -> None:
    body = _file_text(BackendLanguage.NODE, "src", "app", "ports", "error-port.ts")
    assert "interface ErrorPort" in body
    assert "serialize(exc: unknown): ErrorEnvelope" in body
    # The envelope interface declares the same five RFC-007 fields.
    assert "interface ErrorEnvelope" in body
    # Codex Phase B round 1 follow-up: RFC-007 mandates snake_case
    # `correlation_id` — the prior assertion checked camelCase
    # (`correlationId`) which would have been a wire-shape regression
    # vs the existing base-template error-handler. Test + port + adapter
    # now all agree on the snake_case spelling.
    for field in ("code", "message", "type", "context", "correlation_id"):
        assert field in body, f"node port missing envelope field: {field!r}"


def test_node_default_adapter_implements_port() -> None:
    body = _file_text(BackendLanguage.NODE, "src", "app", "adapters", "error-default.ts")
    assert "class DefaultErrorPort implements ErrorPort" in body
    assert "INTERNAL_ERROR" in body
    # The adapter must import AppError — that's the load-bearing
    # decoupling between the port and the base-template error hierarchy.
    assert "AppError" in body


def test_rust_port_declares_serialize_trait() -> None:
    body = _file_text(BackendLanguage.RUST, "src", "error_port", "mod.rs")
    assert "trait ErrorPort" in body
    assert "fn serialize" in body
    assert "struct ErrorEnvelope" in body
    assert "struct ErrorBody" in body
    # Five RFC-007 fields on the body struct. ``type`` is renamed to
    # ``type_name`` (Rust keyword); serde's ``#[serde(rename = "type")]``
    # restores the wire name.
    for field in ("code:", "message:", "type_name:", "context:", "correlation_id:"):
        assert field in body, f"rust port missing envelope field: {field!r}"
    assert 'rename = "type"' in body


def test_rust_default_adapter_implements_port() -> None:
    body = _file_text(BackendLanguage.RUST, "src", "error_port", "default.rs")
    assert "impl ErrorPort for DefaultErrorPort" in body
    assert "INTERNAL_ERROR" in body
    # The adapter must reach into ``crate::errors::AppError`` — that's
    # the load-bearing decoupling between the port and the base-template
    # error hierarchy.
    assert "crate::errors::AppError" in body


# -- envelope wire-shape parity (cross-language) ------------------------------


_REQUIRED_ENVELOPE_FIELDS = ("code", "message", "type", "context", "correlation_id")


def test_envelope_wire_shape_matches_rfc007_across_backends() -> None:
    """The five required envelope fields per RFC-007 §"The envelope" all
    appear in every backend's port + adapter source. This is the
    cross-language wire-shape gate — drift here means the unified
    frontend client breaks."""
    py_adapter = _file_text(BackendLanguage.PYTHON, "src", "app", "adapters", "error_default.py")
    rust_port = _file_text(BackendLanguage.RUST, "src", "error_port", "mod.rs")
    for field in _REQUIRED_ENVELOPE_FIELDS:
        assert field in py_adapter, f"python adapter missing wire field: {field!r}"
        # Rust uses ``type_name`` internally (``type`` is a keyword) but
        # serialises as ``type`` via serde rename — check both.
        rust_field = "type_name" if field == "type" else field
        assert rust_field in rust_port, f"rust port missing wire field: {field!r}"


# -- resolver dispatch --------------------------------------------------------


def _python_project(options: dict[str, object] | None = None) -> ProjectConfig:
    return ProjectConfig(
        project_name="ErrorPortTest",
        backends=[
            BackendConfig(
                name="svc",
                project_name="ErrorPortTest",
                language=BackendLanguage.PYTHON,
                server_port=5000,
            )
        ],
        frontend=None,
        options=options or {},
    )


def test_default_resolution_pulls_in_error_port() -> None:
    """``observability.error_envelope`` defaults to ``True`` — the
    resolver should pull ``error_port`` into every plan unless the user
    explicitly opts out. This is what makes the change backwards-
    compatible at the byte level for existing projects."""
    plan = resolve(_python_project())
    applied = {rf.fragment.name for rf in plan.ordered}
    assert "error_port" in applied


def test_explicit_disable_strips_the_port() -> None:
    """Flipping ``observability.error_envelope=False`` removes the
    fragment from the plan. The base-template error code itself isn't
    stripped yet (that's a follow-up — the strip mechanism for the
    hand-written errors.py / errors.ts / errors.rs files lands in a
    separate PR per the plan)."""
    plan = resolve(_python_project({"observability.error_envelope": False}))
    applied = {rf.fragment.name for rf in plan.ordered}
    assert "error_port" not in applied


def test_explicit_enable_is_a_no_op_versus_default() -> None:
    """Setting the option to ``True`` explicitly produces the same plan
    as omitting it — the default is ``True``. Lock in that there's no
    drift between the two paths."""
    default_plan = resolve(_python_project())
    explicit_plan = resolve(_python_project({"observability.error_envelope": True}))
    default_names = {rf.fragment.name for rf in default_plan.ordered}
    explicit_names = {rf.fragment.name for rf in explicit_plan.ordered}
    assert default_names == explicit_names
