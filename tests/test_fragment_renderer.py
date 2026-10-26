"""Tests for the Pillar A.2 :class:`FragmentRenderer` protocol.

Covers:

- :class:`MiddlewareSpec` round-trips identically through the new
  ``renderers=`` dispatch as through the legacy ``middlewares=``
  keyword (no observable diff in the emitted ``_Injection`` records).
- A heterogeneous renderer tuple — one :class:`MiddlewareSpec` plus
  one stub :class:`FragmentRenderer` standing in for the future
  ``ServiceRegistrationSpec`` — coexists cleanly in a single plan.
- Empty ``renderers=()`` (with or without ``backend``) falls back to
  the no-spec path: ``inject.yaml`` injections only, no synth.
- The legacy ``middlewares=`` keyword is still honoured for callers
  that haven't migrated, including the existing Epic K plugin import
  surface at ``forge.middleware_spec``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from forge.appliers.plan import FragmentPlan, _Injection
from forge.appliers.renderers import FragmentRenderer
from forge.config import BackendLanguage
from forge.fragments import FragmentImplSpec
from forge.specs.middleware import MiddlewareSpec

if TYPE_CHECKING:
    import jinja2

    from forge.appliers.plan import InjectionZone


# ---------------------------------------------------------------------------
# Stub renderer — stands in for the future RFC-009 ``ServiceRegistrationSpec``
# without pulling its (not-yet-implemented) dispatch into this file.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _StubServiceRegistrationSpec:
    """Minimal :class:`FragmentRenderer` for heterogeneous-dispatch tests.

    Emits one injection at the language's service-registration anchor.
    Doesn't exercise the Jinja-macro path the real RFC-009 spec will
    use — that's intentional, the protocol explicitly leaves
    ``jinja_env`` rendering up to the implementer.
    """

    name: str
    backend: BackendLanguage
    order: int = 100
    attach_zone: InjectionZone = "generated"

    def render(
        self,
        *,
        backend: BackendLanguage,
        feature_key: str,
        jinja_env: jinja2.Environment | None = None,  # noqa: ARG002 — protocol parity
    ) -> tuple[_Injection, ...]:
        if backend != self.backend:
            return ()
        return (
            _Injection(
                feature_key=feature_key,
                target="src/app/main.py",
                marker="FORGE:SERVICE_REGISTRATION",
                snippet=f"# register {self.name}",
                position="before",
                zone=self.attach_zone,
            ),
        )


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_middleware_spec_is_a_fragment_renderer(self) -> None:
        spec = MiddlewareSpec(
            name="x",
            backend=BackendLanguage.PYTHON,
            order=50,
            import_snippet="import x",
            register_snippet="app.use(x)",
        )
        assert isinstance(spec, FragmentRenderer)

    def test_stub_service_registration_is_a_fragment_renderer(self) -> None:
        stub = _StubServiceRegistrationSpec(name="svc", backend=BackendLanguage.PYTHON)
        assert isinstance(stub, FragmentRenderer)


# ---------------------------------------------------------------------------
# MiddlewareSpec round-trip — new ``renderers=`` path vs legacy
# ``middlewares=`` path produces identical ``_Injection`` records.
# ---------------------------------------------------------------------------


class TestMiddlewareSpecRoundTrip:
    """Migrating ``middlewares=`` → ``renderers=`` is a no-op."""

    def _spec(self) -> MiddlewareSpec:
        return MiddlewareSpec(
            name="round_trip",
            backend=BackendLanguage.PYTHON,
            order=42,
            import_snippet="from app.mw import RoundTrip",
            register_snippet="app.add_middleware(RoundTrip)",
        )

    def _make_impl(self, tmp_path: Path) -> FragmentImplSpec:
        frag = tmp_path / "round_trip"
        frag.mkdir()
        return FragmentImplSpec(fragment_dir=str(frag))

    def test_renderers_path_emits_same_injections_as_middlewares_path(self, tmp_path: Path) -> None:
        spec = self._spec()
        impl = self._make_impl(tmp_path)

        via_renderers = FragmentPlan.from_impl(
            impl,
            "round_trip",
            renderers=(spec,),
            backend=BackendLanguage.PYTHON,
        )
        via_middlewares = FragmentPlan.from_impl(
            impl,
            "round_trip",
            middlewares=(spec,),
            backend=BackendLanguage.PYTHON,
        )

        assert via_renderers.injections == via_middlewares.injections
        # Sanity — both paths actually produced the expected two injections.
        assert len(via_renderers.injections) == 2
        assert via_renderers.injections[0].marker == "FORGE:MIDDLEWARE_IMPORTS"
        assert via_renderers.injections[1].marker == "FORGE:MIDDLEWARE_REGISTRATION"

    def test_render_method_filters_on_backend_mismatch(self) -> None:
        spec = self._spec()
        node_injs = spec.render(
            backend=BackendLanguage.NODE,
            feature_key="round_trip",
        )
        assert node_injs == ()

    def test_render_method_emits_for_matching_backend(self) -> None:
        spec = self._spec()
        py_injs = spec.render(
            backend=BackendLanguage.PYTHON,
            feature_key="round_trip",
        )
        assert len(py_injs) == 2
        assert py_injs[0].target == "src/app/main.py"

    def test_attach_zone_propagates_to_emitted_injections(self, tmp_path: Path) -> None:
        """A spec authored with ``attach_zone='user'`` stamps that zone on
        every ``_Injection`` it emits — so the downstream zoned dispatch
        in :class:`FragmentInjectionApplier` honours the renderer's
        re-apply semantics, not the default ``"generated"``.
        """
        spec = MiddlewareSpec(
            name="user_zoned",
            backend=BackendLanguage.PYTHON,
            order=50,
            import_snippet="import user_zoned",
            register_snippet="app.use(user_zoned)",
            attach_zone="user",
        )
        impl = self._make_impl(tmp_path)
        plan = FragmentPlan.from_impl(
            impl,
            "user_zoned",
            renderers=(spec,),
            backend=BackendLanguage.PYTHON,
        )
        assert all(inj.zone == "user" for inj in plan.injections)


# ---------------------------------------------------------------------------
# Heterogeneous-renderer dispatch — multiple FragmentRenderer kinds in one
# fragment plan flow through the same loop without special-casing.
# ---------------------------------------------------------------------------


class TestHeterogeneousRenderers:
    def test_middleware_plus_stub_service_registration_coexist(self, tmp_path: Path) -> None:
        frag = tmp_path / "combo"
        frag.mkdir()
        impl = FragmentImplSpec(fragment_dir=str(frag))

        mw = MiddlewareSpec(
            name="combo_mw",
            backend=BackendLanguage.PYTHON,
            order=50,
            import_snippet="import combo_mw",
            register_snippet="app.add_middleware(ComboMW)",
        )
        svc = _StubServiceRegistrationSpec(
            name="combo_svc",
            backend=BackendLanguage.PYTHON,
            order=10,
        )

        plan = FragmentPlan.from_impl(
            impl,
            "combo",
            renderers=(mw, svc),
            backend=BackendLanguage.PYTHON,
        )

        # 2 from middleware + 1 from stub-service = 3 injections total.
        assert len(plan.injections) == 3
        markers = [inj.marker for inj in plan.injections]
        # ``_render_all`` sorts by ``(order, name)``, so combo_svc
        # (order=10) renders before combo_mw (order=50). The single
        # service injection lands first.
        assert markers == [
            "FORGE:SERVICE_REGISTRATION",
            "FORGE:MIDDLEWARE_IMPORTS",
            "FORGE:MIDDLEWARE_REGISTRATION",
        ]

    def test_backend_mismatch_filters_out_wrong_renderers(self, tmp_path: Path) -> None:
        """A fragment shipping renderers for every backend doesn't emit
        Python injections when the active backend is Node."""
        frag = tmp_path / "all_backends"
        frag.mkdir()
        impl = FragmentImplSpec(fragment_dir=str(frag))

        py_mw = MiddlewareSpec(
            name="x",
            backend=BackendLanguage.PYTHON,
            order=50,
            import_snippet="import x",
            register_snippet="app.use(x)",
        )
        node_mw = MiddlewareSpec(
            name="x",
            backend=BackendLanguage.NODE,
            order=50,
            import_snippet="import { x } from './x';",
            register_snippet="await app.register(x);",
        )

        node_plan = FragmentPlan.from_impl(
            impl,
            "x",
            renderers=(py_mw, node_mw),
            backend=BackendLanguage.NODE,
        )
        # Only the Node spec contributes — two injections at src/app.ts.
        assert len(node_plan.injections) == 2
        assert all(inj.target == "src/app.ts" for inj in node_plan.injections)


# ---------------------------------------------------------------------------
# Empty / no-spec fallback — the most common path, must stay zero-cost.
# ---------------------------------------------------------------------------


class TestEmptyFallback:
    def test_empty_renderers_no_backend_returns_only_yaml_injections(self, tmp_path: Path) -> None:
        frag = tmp_path / "yaml_only"
        frag.mkdir()
        (frag / "inject.yaml").write_text(
            "- target: main.py\n  marker: FORGE:DEMO\n  snippet: 'app.x = 1'\n",
            encoding="utf-8",
        )
        impl = FragmentImplSpec(fragment_dir=str(frag))

        plan = FragmentPlan.from_impl(impl, "yaml_only", renderers=())
        assert len(plan.injections) == 1
        assert plan.injections[0].marker == "FORGE:DEMO"

    def test_empty_renderers_with_backend_returns_only_yaml_injections(
        self, tmp_path: Path
    ) -> None:
        frag = tmp_path / "yaml_only_be"
        frag.mkdir()
        (frag / "inject.yaml").write_text(
            "- target: main.py\n  marker: FORGE:DEMO\n  snippet: 'pass'\n",
            encoding="utf-8",
        )
        impl = FragmentImplSpec(fragment_dir=str(frag))
        plan = FragmentPlan.from_impl(
            impl, "yaml_only_be", renderers=(), backend=BackendLanguage.PYTHON
        )
        assert len(plan.injections) == 1

    def test_no_yaml_no_renderers_produces_empty_injections(self, tmp_path: Path) -> None:
        frag = tmp_path / "nothing"
        frag.mkdir()
        impl = FragmentImplSpec(fragment_dir=str(frag))
        plan = FragmentPlan.from_impl(impl, "nothing")
        assert plan.injections == ()

    def test_renderers_without_backend_skips_dispatch(self, tmp_path: Path) -> None:
        """A caller that supplies renderers but forgets ``backend`` gets
        the no-op path (matching the legacy ``middlewares=`` semantics).
        """
        frag = tmp_path / "skipped"
        frag.mkdir()
        impl = FragmentImplSpec(fragment_dir=str(frag))
        spec = MiddlewareSpec(
            name="skipped",
            backend=BackendLanguage.PYTHON,
            order=50,
            import_snippet="import skipped",
            register_snippet="app.use(skipped)",
        )
        plan = FragmentPlan.from_impl(impl, "skipped", renderers=(spec,))
        assert plan.injections == ()


# ---------------------------------------------------------------------------
# Backwards-compatibility shim — the Epic K legacy import path still works,
# plugins published against ``forge.middleware_spec`` keep importing.
# ---------------------------------------------------------------------------


class TestLegacyImportShim:
    def test_legacy_import_path_re_exports_same_class(self) -> None:
        from forge.middleware_spec import MiddlewareSpec as LegacyMiddlewareSpec

        assert LegacyMiddlewareSpec is MiddlewareSpec

    def test_legacy_render_helpers_re_exported(self) -> None:
        from forge.middleware_spec import (
            render_axum_layer,
            render_chi_middleware,
            render_fastapi_middleware,
            render_fastify_plugin,
            render_middleware_injections,
        )
        from forge.specs.middleware import (
            render_axum_layer as new_axum,
        )
        from forge.specs.middleware import (
            render_chi_middleware as new_chi,
        )
        from forge.specs.middleware import (
            render_fastapi_middleware as new_fastapi,
        )
        from forge.specs.middleware import (
            render_fastify_plugin as new_fastify,
        )
        from forge.specs.middleware import (
            render_middleware_injections as new_dispatch,
        )

        assert render_axum_layer is new_axum
        assert render_chi_middleware is new_chi
        assert render_fastapi_middleware is new_fastapi
        assert render_fastify_plugin is new_fastify
        assert render_middleware_injections is new_dispatch


# ---------------------------------------------------------------------------
# Pyright/ty regression — the new ``renderers=`` keyword is exposed at the
# pipeline boundary too, so swapping out an applier via ``FragmentPipeline``
# stays type-safe.
# ---------------------------------------------------------------------------


def test_pipeline_run_accepts_renderers_kwarg() -> None:
    """``FragmentPipeline.run`` must accept ``renderers=`` so a caller
    using the higher-level applier surface (not the raw plan) can also
    migrate off the legacy ``middlewares=`` keyword.

    Smoke-checked via :mod:`inspect` rather than a full pipeline run —
    actual run-the-pipeline tests live in ``tests/appliers/`` and would
    pull in a much heavier fixture for marginal value here.
    """
    import inspect

    from forge.appliers.pipeline import FragmentPipeline

    sig = inspect.signature(FragmentPipeline.run)
    assert "renderers" in sig.parameters
    # Legacy keyword still present for one release.
    assert "middlewares" in sig.parameters


# ---------------------------------------------------------------------------
# Parametrised render dispatch — guard against future regression where a
# new backend value is introduced and the dispatch table forgets to gain
# a renderer (would silently return ``()`` — better to make that visible).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("backend", "expected_target"),
    [
        (BackendLanguage.PYTHON, "src/app/main.py"),
        (BackendLanguage.NODE, "src/app.ts"),
        (BackendLanguage.RUST, "src/app.rs"),
        (BackendLanguage.GO, "internal/app/app.go"),
    ],
)
def test_render_dispatches_to_correct_target_per_backend(
    backend: BackendLanguage,
    expected_target: str,
) -> None:
    spec = MiddlewareSpec(
        name="dispatch",
        backend=backend,
        order=50,
        import_snippet="import dispatch",
        register_snippet="app.use(dispatch)",
    )
    injs = spec.render(backend=backend, feature_key="dispatch")
    # At minimum the imports/registration targets resolve correctly.
    register_inj = next(i for i in injs if i.marker == "FORGE:MIDDLEWARE_REGISTRATION")
    assert register_inj.target == expected_target
