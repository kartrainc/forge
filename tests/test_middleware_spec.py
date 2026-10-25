"""Tests for Epic K's ``MiddlewareSpec`` + per-backend renderers."""

from __future__ import annotations

from forge.config import BackendLanguage
from forge.middleware_spec import (
    MiddlewareSpec,
    render_axum_layer,
    render_chi_middleware,
    render_fastapi_middleware,
    render_fastify_plugin,
    render_middleware_injections,
)


def _mk_spec(
    name: str = "test_mw",
    backend: BackendLanguage = BackendLanguage.PYTHON,
    order: int = 50,
    rust_mod_snippet: str | None = None,
) -> MiddlewareSpec:
    return MiddlewareSpec(
        name=name,
        backend=backend,
        order=order,
        import_snippet=f"import {name}",
        register_snippet=f"app.register({name})",
        rust_mod_snippet=rust_mod_snippet,
    )


# ---------------------------------------------------------------------------
# Per-renderer unit tests
# ---------------------------------------------------------------------------


class TestRenderFastapi:
    def test_emits_two_injections_at_fastapi_anchors(self) -> None:
        spec = _mk_spec(backend=BackendLanguage.PYTHON)
        injs = render_fastapi_middleware(spec, feature_key="test_mw")

        assert len(injs) == 2
        assert injs[0].target == "src/app/main.py"
        assert injs[0].marker == "FORGE:MIDDLEWARE_IMPORTS"
        assert injs[0].position == "before"
        assert injs[0].snippet == "import test_mw"

        assert injs[1].target == "src/app/main.py"
        assert injs[1].marker == "FORGE:MIDDLEWARE_REGISTRATION"
        assert injs[1].position == "before"
        assert injs[1].snippet == "app.register(test_mw)"


class TestRenderFastify:
    def test_emits_two_injections_at_fastify_anchors(self) -> None:
        spec = _mk_spec(backend=BackendLanguage.NODE)
        injs = render_fastify_plugin(spec, feature_key="test_mw")

        assert len(injs) == 2
        assert all(inj.target == "src/app.ts" for inj in injs)
        markers = [inj.marker for inj in injs]
        assert markers == ["FORGE:MIDDLEWARE_IMPORTS", "FORGE:MIDDLEWARE_REGISTRATION"]


class TestRenderAxum:
    def test_emits_two_injections_without_rust_mod_snippet(self) -> None:
        spec = _mk_spec(backend=BackendLanguage.RUST, rust_mod_snippet=None)
        injs = render_axum_layer(spec, feature_key="test_mw")

        assert len(injs) == 2
        assert all(inj.target == "src/app.rs" for inj in injs)

    def test_emits_three_injections_with_rust_mod_snippet(self) -> None:
        spec = _mk_spec(
            backend=BackendLanguage.RUST,
            rust_mod_snippet="pub mod test_mw;",
        )
        injs = render_axum_layer(spec, feature_key="test_mw")

        assert len(injs) == 3
        # The mod declaration comes first so `use crate::middleware::...`
        # below it compiles.
        assert injs[0].target == "src/middleware/mod.rs"
        assert injs[0].marker == "FORGE:MOD_REGISTRATION"
        assert injs[0].position == "after"
        assert injs[0].snippet == "pub mod test_mw;"

        assert injs[1].target == "src/app.rs"
        assert injs[1].marker == "FORGE:MIDDLEWARE_IMPORTS"
        assert injs[2].target == "src/app.rs"
        assert injs[2].marker == "FORGE:MIDDLEWARE_REGISTRATION"


class TestRenderChi:
    def _go_spec(self, name: str, order: int = 50, import_snippet: str = "") -> MiddlewareSpec:
        return MiddlewareSpec(
            name=name,
            backend=BackendLanguage.GO,
            order=order,
            import_snippet=import_snippet,
            register_snippet=f"middleware.{name.title()},",
        )

    def test_emits_only_registration_without_import_snippet(self) -> None:
        injs = render_chi_middleware(self._go_spec("timing"), feature_key="timing")

        assert len(injs) == 1
        assert injs[0].target == "internal/app/app.go"
        assert injs[0].marker == "FORGE:MIDDLEWARE_REGISTRATION"
        assert injs[0].position == "after"
        assert injs[0].snippet == "middleware.Timing,"

    def test_emits_import_inside_import_block_when_set(self) -> None:
        spec = self._go_spec("timing", import_snippet='"time"')
        injs = render_chi_middleware(spec, feature_key="timing")

        assert [i.marker for i in injs] == [
            "FORGE:MIDDLEWARE_IMPORTS",
            "FORGE:MIDDLEWARE_REGISTRATION",
        ]
        assert injs[0].target == "internal/app/app.go"
        assert injs[0].position == "before"
        assert injs[0].snippet == '"time"'

    def test_go_template_carries_both_markers(self) -> None:
        from forge.config import BACKEND_REGISTRY
        from forge.generator import TEMPLATES_DIR

        template = (
            TEMPLATES_DIR
            / BACKEND_REGISTRY[BackendLanguage.GO].template_dir
            / "template"
            / "internal"
            / "app"
            / "app.go.jinja"
        )
        text = template.read_text(encoding="utf-8")
        assert "// FORGE:MIDDLEWARE_IMPORTS" in text
        assert "// FORGE:MIDDLEWARE_REGISTRATION" in text

    def test_higher_order_is_outermost_in_the_middleware_list(self, tmp_path) -> None:
        from forge.appliers.injection import _apply_zoned_injection

        app_go = tmp_path / "app.go"
        app_go.write_text(
            "package app\n\n"
            "import (\n"
            '\t"net/http"\n'
            "\t// FORGE:MIDDLEWARE_IMPORTS\n"
            ")\n\n"
            "var middlewares = []func(http.Handler) http.Handler{\n"
            "\tmiddleware.Correlation,\n"
            "\t// FORGE:MIDDLEWARE_REGISTRATION\n"
            "}\n",
            encoding="utf-8",
        )
        specs = (
            self._go_spec("inner", order=50),
            self._go_spec("outer", order=80, import_snippet='"time"'),
        )
        for spec in specs:
            injs = render_middleware_injections((spec,), BackendLanguage.GO, spec.name)
            for inj in injs:
                _apply_zoned_injection(app_go, inj)

        lines = [line.strip() for line in app_go.read_text(encoding="utf-8").splitlines()]
        assert lines.index('"time"') < lines.index(")")
        assert (
            lines.index("middleware.Correlation,")
            < lines.index("middleware.Outer,")
            < lines.index("middleware.Inner,")
        )


# ---------------------------------------------------------------------------
# Dispatch + filtering
# ---------------------------------------------------------------------------


class TestRenderMiddlewareInjections:
    def test_filters_by_backend(self) -> None:
        specs = (
            _mk_spec("a", backend=BackendLanguage.PYTHON),
            _mk_spec("b", backend=BackendLanguage.NODE),
            _mk_spec("c", backend=BackendLanguage.PYTHON),
        )
        python_injs = render_middleware_injections(specs, BackendLanguage.PYTHON, "f")
        # Only two specs match → 4 injections total (2 per spec).
        assert len(python_injs) == 4

        node_injs = render_middleware_injections(specs, BackendLanguage.NODE, "f")
        assert len(node_injs) == 2

    def test_deterministic_order_by_order_then_name(self) -> None:
        specs = (
            _mk_spec("z_high_order", order=80, backend=BackendLanguage.PYTHON),
            _mk_spec("a_low_order", order=50, backend=BackendLanguage.PYTHON),
            _mk_spec("m_low_order", order=50, backend=BackendLanguage.PYTHON),
        )
        injs = render_middleware_injections(specs, BackendLanguage.PYTHON, "f")
        # Expected order: a_low_order (50, a) → m_low_order (50, m) →
        # z_high_order (80, z). Each spec produces 2 injections, so the
        # resulting import snippets should match that order.
        import_snippets = [i.snippet for i in injs if i.marker == "FORGE:MIDDLEWARE_IMPORTS"]
        assert import_snippets == [
            "import a_low_order",
            "import m_low_order",
            "import z_high_order",
        ]

    def test_empty_specs_produce_no_injections(self) -> None:
        injs = render_middleware_injections((), BackendLanguage.PYTHON, "f")
        assert injs == ()

    def test_unknown_backend_produces_no_injections(self) -> None:
        # A plugin-added language with no registered renderer returns ()
        # instead of raising — keeps a hypothetical Go/Java plugin from
        # breaking the apply pipeline during its development phase.
        # We simulate by pretending a spec's backend has no renderer
        # (can't actually add a new enum in a test, but the contract is
        # still useful to document + exercise by empty-tuple input).
        specs = (_mk_spec(backend=BackendLanguage.PYTHON),)
        # Passing a backend with no specs matches the "unknown" semantics.
        injs = render_middleware_injections(specs, BackendLanguage.NODE, "f")
        assert injs == ()


# ---------------------------------------------------------------------------
# Integration with FragmentPlan
# ---------------------------------------------------------------------------


class TestFragmentPlanIntegration:
    """FragmentPlan.from_impl expands middlewares into synth injections."""

    def test_plan_appends_middleware_injections(self, tmp_path) -> None:
        from forge.appliers.plan import FragmentPlan
        from forge.fragments import FragmentImplSpec

        frag_dir = tmp_path / "mw_only"
        frag_dir.mkdir()
        impl = FragmentImplSpec(fragment_dir=str(frag_dir))

        spec = _mk_spec(name="mw_only", backend=BackendLanguage.PYTHON)
        plan = FragmentPlan.from_impl(
            impl,
            "mw_only",
            middlewares=(spec,),
            backend=BackendLanguage.PYTHON,
        )
        # No inject.yaml, no files, no deps — just the 2 synth injections.
        assert len(plan.injections) == 2
        assert all(i.target == "src/app/main.py" for i in plan.injections)

    def test_plan_without_backend_skips_middleware_expansion(self, tmp_path) -> None:
        """Absent ``backend`` means the caller didn't know which backend
        context to render for — no expansion, no error."""
        from forge.appliers.plan import FragmentPlan
        from forge.fragments import FragmentImplSpec

        frag_dir = tmp_path / "mw_skipped"
        frag_dir.mkdir()
        impl = FragmentImplSpec(fragment_dir=str(frag_dir))

        spec = _mk_spec(name="mw_skipped", backend=BackendLanguage.PYTHON)
        plan = FragmentPlan.from_impl(impl, "mw_skipped", middlewares=(spec,))
        assert plan.injections == ()

    def test_plan_combines_inject_yaml_and_middlewares(self, tmp_path) -> None:
        from forge.appliers.plan import FragmentPlan
        from forge.fragments import FragmentImplSpec

        frag_dir = tmp_path / "combo"
        frag_dir.mkdir()
        (frag_dir / "inject.yaml").write_text(
            "- target: src/app/main.py\n"
            "  marker: FORGE:HANDLERS\n"
            "  snippet: 'app.include_router(extra)'\n",
            encoding="utf-8",
        )

        impl = FragmentImplSpec(fragment_dir=str(frag_dir))
        spec = _mk_spec(name="combo", backend=BackendLanguage.PYTHON)
        plan = FragmentPlan.from_impl(
            impl,
            "combo",
            middlewares=(spec,),
            backend=BackendLanguage.PYTHON,
        )
        # 1 inject.yaml + 2 synth = 3 injections. YAML ones come first so
        # fragments can author "inject.yaml first, middlewares last" and
        # get stable insertion semantics.
        assert len(plan.injections) == 3
        assert plan.injections[0].marker == "FORGE:HANDLERS"
        assert plan.injections[1].marker == "FORGE:MIDDLEWARE_IMPORTS"
        assert plan.injections[2].marker == "FORGE:MIDDLEWARE_REGISTRATION"


# ---------------------------------------------------------------------------
# Real correlation_id fragment sanity
# ---------------------------------------------------------------------------


def test_correlation_id_fragment_migrated_to_middleware_spec() -> None:
    """Verify the migrated correlation_id fragment has the expected shape.

    correlation_id/python/inject.yaml no longer exists; the import +
    registration lines come from a MiddlewareSpec on the Fragment.
    """
    from forge.fragments import FRAGMENT_REGISTRY

    frag = FRAGMENT_REGISTRY["correlation_id"]
    assert len(frag.middlewares) == 1
    mw = frag.middlewares[0]
    assert mw.name == "correlation_id"
    assert mw.backend == BackendLanguage.PYTHON
    assert mw.order == 90
    assert "CorrelationIdMiddleware" in mw.import_snippet
    assert "app.add_middleware" in mw.register_snippet


def test_correlation_id_inject_yaml_removed() -> None:
    """The legacy inject.yaml file is gone — MiddlewareSpec is authoritative."""
    from forge.fragments import fragments_root

    path = fragments_root() / "correlation_id" / "python" / "inject.yaml"
    assert not path.exists(), (
        "correlation_id/python/inject.yaml should have been removed when "
        "the fragment migrated to MiddlewareSpec. If you're re-adding it, "
        "also remove the MiddlewareSpec from fragments.py."
    )
