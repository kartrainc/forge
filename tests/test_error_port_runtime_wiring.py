"""Tests for Pillar E.1.b — wiring the ``error_port`` adapter into each
backend's central error-handler.

PR #89 shipped the port + default adapter as scaffolding only. This
follow-up routes the runtime request path through
``DefaultErrorPort.serialize`` so the port actually executes when an
exception escapes a handler. The wire shape stays RFC-007 — the goal
isn't a behavioural change for default deployments, it's making the
swappable adapter SLot reachable from production code (custom-envelope
plugins now have a real seam to override).

The runtime wiring is controlled per-backend by the Copier var
``include_error_envelope`` (Jinja-templated handler files). The var
defaults to ``False`` (fail-safe — see codex Phase B round 1 finding
#1): the generator flips it to ``True`` only when the ``error_port``
fragment is in the active plan, because the Rust template emits a
``use crate::error_port::DefaultErrorPort`` line that won't compile
when the port module isn't on disk. The Python soft-import + Node
``ERR_MODULE_NOT_FOUND`` guard are the defence-in-depth for the
in-plan path; the false default keeps out-of-plan generations
byte-clean.

The behavioural cross-language envelope-shape test that codex flagged
on PR #89 (round 1 finding #1) is satisfied by the per-backend
integration tests under each template's ``tests/`` directory — those
spin up the real framework and assert the JSON wire shape EXACTLY.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forge import variable_mapper
from forge.config import BackendConfig, BackendLanguage

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_ROOT = REPO_ROOT / "forge" / "templates" / "services"

PYTHON_HANDLER = TEMPLATES_ROOT / "python-service-template/template/src/app/core/errors.py.jinja"
NODE_HANDLER = (
    TEMPLATES_ROOT / "node-service-template/template/src/middleware/error-handler.ts.jinja"
)
# Rust errors.rs stays as a plain (non-Jinja) file: the default-false
# ``include_error_envelope`` path is the ONLY path for Rust today and
# it requires no conditional compilation. Promoting to .jinja is a
# follow-up once the ``error_port`` fragment grows a Rust adapter and
# ``include_error_envelope=True`` must emit the port-wired branch.


def _render_jinja(template_path: Path, *, include_error_envelope: bool) -> str:
    """Render a Jinja template with ONLY the wiring flag set.

    Doesn't pull copier into the test path — the handler templates use a
    tiny subset of Jinja that ``jinja2`` renders directly. Keeps this
    test independent of Copier's rendering pipeline (which is exercised
    end-to-end by the matrix tests).
    """
    from jinja2 import Environment  # noqa: PLC0415

    env = Environment(
        keep_trailing_newline=True,
        autoescape=False,
        # Match Copier defaults so block tags etc. behave identically.
        trim_blocks=False,
        lstrip_blocks=False,
    )
    template = env.from_string(template_path.read_text(encoding="utf-8"))
    return template.render(include_error_envelope=include_error_envelope)


# -- handler files exist as .jinja (port-wiring is template-conditional) -----


@pytest.mark.parametrize(
    "handler",
    [PYTHON_HANDLER, NODE_HANDLER],
    ids=["python", "node"],
)
def test_central_handler_is_jinja_template(handler: Path) -> None:
    """The handler files must be Jinja templates so the port-wiring
    branch is conditionally compiled in/out. A regression to a static
    ``.py`` / ``.ts`` / ``.rs`` file would lose the disable-via-option
    knob."""
    assert handler.is_file(), f"central handler missing: {handler}"


# -- Python handler — Jinja conditional ---------------------------------------


def test_python_handler_imports_default_error_port_when_enabled() -> None:
    """With ``include_error_envelope=True`` the generated Python
    handler imports ``DefaultErrorPort`` from the fragment-shipped
    adapter and binds it to the ``_serialize_exception`` seam — the
    runtime path actually exercises the port."""
    rendered = _render_jinja(PYTHON_HANDLER, include_error_envelope=True)
    assert "from app.adapters.error_default import DefaultErrorPort" in rendered
    assert "_serialize_exception = DefaultErrorPort().serialize" in rendered
    # Soft-import safety net stays in place — the disabled fragment
    # case never crashes startup on a stale flag.
    assert "ImportError" in rendered


def test_python_handler_stays_inline_when_disabled() -> None:
    """With ``include_error_envelope=False`` the handler doesn't
    reference the fragment-shipped adapter at all — the port files
    aren't on disk in that mode and a stray import would break startup.
    The inline ``_default_serialize_exception`` owns the wire shape."""
    rendered = _render_jinja(PYTHON_HANDLER, include_error_envelope=False)
    assert "from app.adapters.error_default import DefaultErrorPort" not in rendered
    assert "_serialize_exception = _default_serialize_exception" in rendered


def test_python_handler_central_seam_is_called_from_domain_handler() -> None:
    """Both branches must funnel ``ApplicationError`` through
    ``_serialize_exception`` — that's the seam the port adapter binds
    to. If the central handler regresses to inline envelope
    construction we lose the runtime wiring."""
    for flag in (True, False):
        rendered = _render_jinja(PYTHON_HANDLER, include_error_envelope=flag)
        assert "_serialize_exception(exc)" in rendered, (
            f"domain handler must delegate to _serialize_exception (include_error_envelope={flag})"
        )
        assert "def status_for_code(code:" in rendered


# -- Node handler — Jinja conditional -----------------------------------------


def test_node_handler_dynamic_imports_adapter_when_enabled() -> None:
    """With ``include_error_envelope=True`` the Node handler
    dynamically imports the fragment-shipped adapter so the port is
    on the runtime path. The soft try/catch keeps the inline fallback
    available — same backwards-compat invariant as Python."""
    rendered = _render_jinja(NODE_HANDLER, include_error_envelope=True)
    assert 'import("../app/adapters/error-default.js")' in rendered
    assert "new mod.DefaultErrorPort()" in rendered
    # Soft import catches the missing-file case.
    assert "} catch" in rendered


def test_node_handler_stays_inline_when_disabled() -> None:
    """With ``include_error_envelope=False`` the dynamic import is
    stripped and the handler binds straight to ``defaultSerialize``."""
    rendered = _render_jinja(NODE_HANDLER, include_error_envelope=False)
    assert 'import("../app/adapters/error-default.js")' not in rendered
    assert "_serializeException: SerializeFn = defaultSerialize" in rendered


def test_node_handler_central_seam_is_called_from_app_error_branch() -> None:
    """Both branches must funnel ``AppError`` through
    ``_serializeException`` — the seam the port adapter overwrites."""
    for flag in (True, False):
        rendered = _render_jinja(NODE_HANDLER, include_error_envelope=flag)
        assert "_serializeException(error)" in rendered, (
            f"errorHandler must delegate to _serializeException (include_error_envelope={flag})"
        )
        assert "statusForCode(error.code)" in rendered


# -- Rust handler — plain (non-Jinja) file; no conditional wiring tests. --
# The Rust ``errors.rs`` stays static until the ``error_port`` fragment
# ships a Rust adapter (Pillar E follow-up). At that point the file
# promotes to ``.rs.jinja`` and gains the ``include_error_envelope``
# conditional mirroring Python + Node.


# -- variable_mapper threading ------------------------------------------------


def test_backend_context_defaults_include_error_envelope_to_false() -> None:
    """The default must be ``False`` (fail-safe — codex Phase B round 1
    finding #1). The generator promotes to ``True`` only when the
    ``error_port`` fragment is in the active plan; otherwise the Rust
    template would emit a ``use crate::error_port::...`` line that
    fails ``cargo check`` because the port module isn't on disk."""
    bc = BackendConfig(
        name="svc",
        project_name="ErrorPortWiringTest",
        language=BackendLanguage.PYTHON,
        server_port=5000,
    )
    ctx = variable_mapper.backend_context(bc)
    assert ctx["include_error_envelope"] is False


def test_backend_context_threads_include_error_envelope_override() -> None:
    """The kwarg is honoured when the generator sets it — the wiring in
    ``generator.py:_generate_main`` flips to ``True`` based on plan
    membership of ``error_port``."""
    bc = BackendConfig(
        name="svc",
        project_name="ErrorPortWiringTest",
        language=BackendLanguage.PYTHON,
        server_port=5000,
    )
    ctx = variable_mapper.backend_context(bc, include_error_envelope=True)
    assert ctx["include_error_envelope"] is True


def test_backend_context_threads_per_backend() -> None:
    """All three backends must receive the same flag — cross-stack
    runtime-wiring parity. A Python-only flag would silently leave
    Node + Rust on the inline path."""
    for lang in (BackendLanguage.PYTHON, BackendLanguage.NODE, BackendLanguage.RUST):
        bc = BackendConfig(
            name="svc",
            project_name="ErrorPortWiringTest",
            language=lang,
            server_port=5000,
        )
        ctx = variable_mapper.backend_context(bc, include_error_envelope=True)
        assert ctx["include_error_envelope"] is True


def test_generator_only_enables_envelope_when_fragment_in_plan() -> None:
    """Codex Phase B round 1 finding #1: the generator must derive
    ``include_error_envelope`` from plan membership of the
    ``error_port`` fragment, not pass the variable_mapper default
    through. Anything else risks shipping the port-wired branch on
    projects whose plan never resolved the adapter."""
    from forge import generator  # noqa: PLC0415
    import inspect  # noqa: PLC0415

    src = inspect.getsource(generator)
    # The guard must use the same plan-membership pattern as
    # ``include_platform_auth`` — a single source-of-truth for the
    # whole gating idiom.
    assert "includes_error_envelope" in src, (
        "generator must compute include_error_envelope from plan membership"
    )
    assert 'rf.fragment.name == "error_port"' in src, (
        "generator must guard on the error_port fragment name specifically"
    )
    assert "include_error_envelope=includes_error_envelope" in src, (
        "_generate_single_backend must receive the derived value"
    )


def test_add_backend_derives_envelope_from_manifest_options() -> None:
    """Codex Phase B round 2 finding (A): the ``forge add-backend``
    code path must thread ``include_error_envelope`` derived from the
    project's stored ``observability.error_envelope`` option. Without
    this, a project that opted into the port wiring silently ships its
    new backend without the wiring (the inline serialiser kicks in),
    breaking per-project consistency."""
    from forge.cli.commands import add_backend  # noqa: PLC0415
    import inspect  # noqa: PLC0415

    src = inspect.getsource(add_backend)
    assert "observability.error_envelope" in src, (
        "add-backend must read the option from the manifest"
    )
    assert "include_error_envelope=include_error_envelope" in src, (
        "add-backend must pass the derived value to _generate_single_backend"
    )


def test_node_handler_only_catches_module_not_found() -> None:
    """Codex Phase B round 1 finding #2: the dynamic-import try/catch
    must rethrow non-module-not-found errors. Bare ``catch {}`` would
    mask real adapter init bugs (syntax errors, constructor throws),
    silently degrading to the inline serialiser and hiding the bug
    until someone notices the missing port instrumentation."""
    rendered = _render_jinja(NODE_HANDLER, include_error_envelope=True)
    assert "ERR_MODULE_NOT_FOUND" in rendered, (
        "Node soft-import must check ERR_MODULE_NOT_FOUND specifically"
    )
    assert "throw err" in rendered or "throw error" in rendered, (
        "non-module-not-found errors must propagate, not be swallowed"
    )


def test_python_handler_softimport_executes_at_runtime() -> None:
    """Codex Phase B round 1 finding #4: structural string assertions
    don't prove the runtime fallback actually runs. Render the Python
    handler to a tmp file, exec it twice — once without
    ``app.adapters.error_default`` importable (must fall through to
    ``_default_serialize_exception``), once with a stub adapter (must
    bind to the stub's ``serialize``). Exercises the actual
    try/except/else branches end-to-end."""
    import sys  # noqa: PLC0415
    import types  # noqa: PLC0415

    rendered = _render_jinja(PYTHON_HANDLER, include_error_envelope=True)

    # Strip the FastAPI / domain-handler portion — we only need the
    # serialiser-seam block to run. The seam block is delimited by the
    # well-known ``_default_serialize_exception`` def above and
    # ``def status_for_code`` below; isolate the import + binding lines.
    seam_marker = "# Port-wired path:"
    bind_marker = "_serialize_exception = "
    assert seam_marker in rendered
    seam_start = rendered.index(seam_marker)
    # The block ends at the first ``def `` after the binding lines.
    bind_idx = rendered.index(bind_marker, seam_start)
    block_end = rendered.index("\ndef ", bind_idx)
    block = rendered[seam_start:block_end]

    # --- case 1: adapter absent → fallback bound ----------------------
    sys.modules.pop("app", None)
    sys.modules.pop("app.adapters", None)
    sys.modules.pop("app.adapters.error_default", None)
    namespace_absent: dict[str, object] = {
        "_default_serialize_exception": lambda exc: {"sentinel": "default"},
    }
    exec(compile(block, "<rendered-py-absent>", "exec"), namespace_absent)
    fn_absent = namespace_absent["_serialize_exception"]
    assert callable(fn_absent)
    assert fn_absent(RuntimeError("x")) == {"sentinel": "default"}, (
        "with adapter absent, seam must bind to _default_serialize_exception"
    )

    # --- case 2: adapter present → port bound -------------------------
    app_mod = types.ModuleType("app")
    adapters_mod = types.ModuleType("app.adapters")
    adapter_mod = types.ModuleType("app.adapters.error_default")

    class _StubPort:
        def serialize(self, exc: object) -> dict[str, str]:
            return {"sentinel": "port"}

    adapter_mod.DefaultErrorPort = _StubPort  # type: ignore[attr-defined]
    sys.modules["app"] = app_mod
    sys.modules["app.adapters"] = adapters_mod
    sys.modules["app.adapters.error_default"] = adapter_mod
    try:
        namespace_present: dict[str, object] = {
            "_default_serialize_exception": lambda exc: {"sentinel": "default"},
        }
        exec(compile(block, "<rendered-py-present>", "exec"), namespace_present)
        fn_present = namespace_present["_serialize_exception"]
        assert callable(fn_present)
        assert fn_present(RuntimeError("x")) == {"sentinel": "port"}, (
            "with adapter present, seam must bind to DefaultErrorPort().serialize"
        )
    finally:
        sys.modules.pop("app.adapters.error_default", None)
        sys.modules.pop("app.adapters", None)
        sys.modules.pop("app", None)


# -- wire-shape invariant across all three rendered handlers ------------------


_RFC007_FIELDS = ("code", "message", "type", "context", "correlation_id")


@pytest.mark.parametrize(
    ("handler", "context_label"),
    [
        (PYTHON_HANDLER, "python-port-on"),
        (NODE_HANDLER, "node-port-on"),
    ],
)
def test_rendered_handler_keeps_rfc007_wire_shape(handler: Path, context_label: str) -> None:
    """When the port is wired (``include_error_envelope=True``), the
    rendered handler must reference every RFC-007 envelope field —
    otherwise the wire shape drifts away from the canonical contract
    the unified frontend client depends on."""
    rendered = _render_jinja(handler, include_error_envelope=True)
    for field in _RFC007_FIELDS:
        needle = field
        assert needle in rendered, (
            f"{context_label}: rendered handler missing RFC-007 wire field "
            f"{field!r} (looking for {needle!r})"
        )


GO_APP = TEMPLATES_ROOT / "go-service-template/template/internal/app/app.go.jinja"


@pytest.mark.parametrize("include_error_envelope", [True, False])
def test_go_composition_root_wires_error_port_only_in_plan(include_error_envelope: bool) -> None:
    from jinja2 import Environment  # noqa: PLC0415

    rendered = (
        Environment(keep_trailing_newline=True, autoescape=False)
        .from_string(GO_APP.read_text(encoding="utf-8"))
        .render(
            include_error_envelope=include_error_envelope,
            include_platform_auth=False,
            project_name="orders",
            project_description="Orders",
        )
    )
    assert ("useErrorPort()" in rendered) is include_error_envelope
