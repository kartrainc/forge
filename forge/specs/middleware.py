"""Cross-language middleware registration spec.

Originally shipped at ``forge.middleware_spec`` for Epic K (1.1.0-alpha.1);
relocated to ``forge.specs.middleware`` in 1.3.0 as the first
:class:`~forge.appliers.renderers.FragmentRenderer` implementation under
Pillar A.2. The legacy import path remains available via a 1-line shim
in ``forge/middleware_spec.py`` (slated for removal in 2.0).

Every middleware fragment forge ships has the same shape per backend: add
an import line at the language's middleware-imports anchor, and add a
registration block at the middleware-registration anchor. The per-backend
idioms differ (``app.add_middleware(X)`` for FastAPI, ``await
app.register(X, ...)`` for Fastify, ``.layer(X)`` for Axum, a
``middleware.X,`` list entry for Go/chi) but the structure is identical.

Before Epic K, each fragment shipped one ``inject.yaml`` per backend that
re-encoded the same ceremony with small language-specific tweaks. Adding a
new middleware meant writing three near-identical YAML files; adding a new
backend meant writing six near-identical YAML files for every existing
middleware. Epic K replaced that with one Python dataclass + one snippet per
backend, and a per-backend renderer that emits the right ``_Injection``
records at plan time.

Pillar A.2 generalises that dispatch through the
:class:`~forge.appliers.renderers.FragmentRenderer` protocol on
:meth:`FragmentPlan.from_impl`, so future declarative spec types can flow
through the same path. ``MiddlewareSpec`` is the only implementer today.

Usage shape inside ``forge/fragments.py``::

    register_fragment(
        Fragment(
            name="correlation_id",
            order=90,
            implementations={
                BackendLanguage.PYTHON: FragmentImplSpec(
                    fragment_dir="correlation_id/python",
                ),
            },
            middlewares=(
                MiddlewareSpec(
                    name="correlation_id",
                    backend=BackendLanguage.PYTHON,
                    order=90,
                    import_snippet=(
                        "from app.middleware.correlation import CorrelationIdMiddleware"
                    ),
                    register_snippet=(
                        "# Correlation ID (outermost — runs first)\\n"
                        "app.add_middleware(CorrelationIdMiddleware)"
                    ),
                ),
            ),
        )
    )

Epic J (Node+Rust ops parity) is the primary consumer — adding a Python
middleware to Node + Rust becomes "three ``MiddlewareSpec`` literals" instead
of "three copy-pasted ``inject.yaml`` files".
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from forge.config import BackendLanguage

if TYPE_CHECKING:
    import jinja2

    from forge.appliers.plan import InjectionZone, _Injection


@dataclass(frozen=True)
class MiddlewareSpec:
    """One middleware registration, targeting one backend.

    Implements the :class:`~forge.appliers.renderers.FragmentRenderer`
    protocol — :meth:`FragmentPlan.from_impl` accepts a tuple of any
    ``FragmentRenderer`` instances and dispatches each one's
    :meth:`render` uniformly.

    Attributes:
        name: Fragment-scoped identifier, used in ``FORGE:BEGIN`` /
            ``FORGE:END`` sentinels. Typically matches ``Fragment.name``.
        backend: Which backend this spec applies to. A fragment ships one
            spec per backend it supports.
        order: Insertion order. Lower renders earlier. Middleware fragments
            canonically order by "outermost first": ``correlation_id=90``,
            ``security_headers=80``, ``rate_limit=50``. The `injections`
            produced for a fragment are stable across runs because renderers
            sort by ``(order, name)``. Across fragments the resolver's apply
            order (``Fragment.order`` within a dependency tier) decides, so
            keep the two equal. On Go the order only ranks a spec against
            other entries after the registration marker; see
            :func:`render_chi_middleware`.
        import_snippet: The import line injected at the language's
            ``FORGE:MIDDLEWARE_IMPORTS`` anchor. Go may leave it empty: its
            middleware lives in the already-imported ``middleware`` package.
        register_snippet: The registration block injected at the language's
            ``FORGE:MIDDLEWARE_REGISTRATION`` anchor. May span multiple
            lines — include a leading comment as part of the snippet if you
            want one emitted above the call.
        rust_mod_snippet: Rust-specific. Added at ``FORGE:MOD_REGISTRATION``
            in ``src/middleware/mod.rs`` to expose the submodule. Leave
            ``None`` on non-Rust specs or when the middleware lives in a
            sibling module that's already declared.
        attach_zone: The injection zone every record this spec emits lands
            in. Defaults to ``"generated"`` — re-generation overwrites,
            matching the pre-Pillar-A behaviour. Authors who want their
            middleware registration tracked as user-editable can pass
            ``"user"``; ``"merge"`` opts into three-way merge on update.
    """

    name: str
    backend: BackendLanguage
    order: int
    import_snippet: str
    register_snippet: str
    rust_mod_snippet: str | None = None
    attach_zone: InjectionZone = "generated"

    def render(
        self,
        *,
        backend: BackendLanguage,
        feature_key: str,
        jinja_env: jinja2.Environment | None = None,  # noqa: ARG002 — protocol parity
    ) -> tuple[_Injection, ...]:
        """Emit injection records via the matching per-backend renderer.

        Returns ``()`` when ``backend`` doesn't match :attr:`backend` or
        when no renderer is registered for the requested backend (so a
        plugin-introduced language without a built-in renderer fails
        gracefully). The ``jinja_env`` kwarg is unused — middleware
        snippets are verbatim strings — but accepted so the
        :class:`~forge.appliers.renderers.FragmentRenderer` protocol stays
        uniform across spec kinds.
        """
        if backend != self.backend:
            return ()
        renderer = _RENDERERS.get(backend)
        if renderer is None:
            return ()
        return renderer(self, feature_key)


# ---------------------------------------------------------------------------
# Per-backend renderers — each produces a tuple of `_Injection` records.
# ---------------------------------------------------------------------------


def _fastapi_target() -> str:
    return "src/app/main.py"


def _fastify_target() -> str:
    return "src/app.ts"


def _axum_target() -> str:
    return "src/app.rs"


def _axum_mod_target() -> str:
    return "src/middleware/mod.rs"


def _chi_target() -> str:
    return "internal/app/app.go"


def render_fastapi_middleware(spec: MiddlewareSpec, feature_key: str) -> tuple[_Injection, ...]:
    """Emit _Injection records for a Python/FastAPI middleware.

    Both injections land at ``src/app/main.py`` with ``position=before`` —
    that's the canonical layout every ``middleware_*`` fragment already uses.
    """
    from forge.appliers.plan import _Injection  # noqa: PLC0415 — late import

    return (
        _Injection(
            feature_key=feature_key,
            target=_fastapi_target(),
            marker="FORGE:MIDDLEWARE_IMPORTS",
            snippet=spec.import_snippet,
            position="before",
            zone=spec.attach_zone,
        ),
        _Injection(
            feature_key=feature_key,
            target=_fastapi_target(),
            marker="FORGE:MIDDLEWARE_REGISTRATION",
            snippet=spec.register_snippet,
            position="before",
            zone=spec.attach_zone,
        ),
    )


def render_fastify_plugin(spec: MiddlewareSpec, feature_key: str) -> tuple[_Injection, ...]:
    """Emit _Injection records for a Node/Fastify plugin registration."""
    from forge.appliers.plan import _Injection  # noqa: PLC0415

    return (
        _Injection(
            feature_key=feature_key,
            target=_fastify_target(),
            marker="FORGE:MIDDLEWARE_IMPORTS",
            snippet=spec.import_snippet,
            position="before",
            zone=spec.attach_zone,
        ),
        _Injection(
            feature_key=feature_key,
            target=_fastify_target(),
            marker="FORGE:MIDDLEWARE_REGISTRATION",
            snippet=spec.register_snippet,
            position="before",
            zone=spec.attach_zone,
        ),
    )


def render_axum_layer(spec: MiddlewareSpec, feature_key: str) -> tuple[_Injection, ...]:
    """Emit _Injection records for a Rust/Axum tower layer.

    Adds the mod declaration at ``src/middleware/mod.rs`` (position=after)
    before the ``src/app.rs`` import + layer registration.
    """
    from forge.appliers.plan import _Injection  # noqa: PLC0415

    out: list[_Injection] = []
    if spec.rust_mod_snippet is not None:
        out.append(
            _Injection(
                feature_key=feature_key,
                target=_axum_mod_target(),
                marker="FORGE:MOD_REGISTRATION",
                snippet=spec.rust_mod_snippet,
                position="after",
                zone=spec.attach_zone,
            )
        )
    out.append(
        _Injection(
            feature_key=feature_key,
            target=_axum_target(),
            marker="FORGE:MIDDLEWARE_IMPORTS",
            snippet=spec.import_snippet,
            position="before",
            zone=spec.attach_zone,
        )
    )
    out.append(
        _Injection(
            feature_key=feature_key,
            target=_axum_target(),
            marker="FORGE:MIDDLEWARE_REGISTRATION",
            snippet=spec.register_snippet,
            position="before",
            zone=spec.attach_zone,
        )
    )
    return tuple(out)


def render_chi_middleware(spec: MiddlewareSpec, feature_key: str) -> tuple[_Injection, ...]:
    """Emit _Injection records for a Go/chi (net/http) middleware.

    ``register_snippet`` is one or more entries of the
    ``[]middleware.Middleware{...}`` list in ``internal/app/app.go``, each
    ending with a comma (e.g. ``"middleware.Timeout(30 * time.Second),"``).
    The list's first entry is the outermost middleware. Registrations go
    ``after`` the marker, so each one applied later lands above the earlier
    ones and a higher ``order`` is outermost, as on the other backends, but
    only among the entries after the marker: ``MiddlewareSpec``
    registrations and ``rate_limit``. The built-in Go fragments register
    ``before`` the marker in ``inject.yaml`` (security headers, tracing,
    metrics, platform auth, tenant RLS), so every one of them wraps a
    ``MiddlewareSpec`` entry whatever its ``order``. The base
    ``devIdentity`` (``auth.mode=none``) stays innermost.

    Snippets are inserted verbatim, so they can only reference packages
    ``app.go`` imports. The module path is the backend name, so a
    middleware's code belongs in ``internal/middleware`` (package
    ``middleware``, already imported). ``import_snippet`` is optional: when
    set (a stdlib or third-party import spec such as ``"time"``), it lands
    inside the import block at ``FORGE:MIDDLEWARE_IMPORTS``.
    """
    from forge.appliers.plan import _Injection  # noqa: PLC0415

    out: list[_Injection] = []
    if spec.import_snippet.strip():
        out.append(
            _Injection(
                feature_key=feature_key,
                target=_chi_target(),
                marker="FORGE:MIDDLEWARE_IMPORTS",
                snippet=spec.import_snippet,
                position="before",
                zone=spec.attach_zone,
            )
        )
    out.append(
        _Injection(
            feature_key=feature_key,
            target=_chi_target(),
            marker="FORGE:MIDDLEWARE_REGISTRATION",
            snippet=spec.register_snippet,
            position="after",
            zone=spec.attach_zone,
        )
    )
    return tuple(out)


# Dispatch table. Adding a new backend (Java, Elixir) = register a
# renderer here. The INJECTOR_REGISTRY pattern Epic B introduces for AST
# injectors would subsume this in a future unification, but for Epic K the
# dispatch is small enough to keep explicit.
_RENDERERS: dict[
    BackendLanguage,
    Callable[[MiddlewareSpec, str], tuple[_Injection, ...]],
] = {
    BackendLanguage.PYTHON: render_fastapi_middleware,
    BackendLanguage.NODE: render_fastify_plugin,
    BackendLanguage.RUST: render_axum_layer,
    BackendLanguage.GO: render_chi_middleware,
}


def render_middleware_injections(
    middlewares: tuple[MiddlewareSpec, ...],
    backend: BackendLanguage,
    feature_key: str,
) -> tuple[_Injection, ...]:
    """Synth injections for every middleware targeting ``backend``.

    Returns injections in deterministic order: ``(spec.order, spec.name)``.
    Specs whose ``backend`` doesn't match the target are silently skipped —
    the applier runs once per backend and only the matching specs fire.
    Unknown backends (e.g. a plugin language with no renderer registered)
    return ``()`` so the injection phase doesn't raise.
    """
    renderer = _RENDERERS.get(backend)
    if renderer is None:
        return ()
    specs = sorted(
        (m for m in middlewares if m.backend == backend),
        key=lambda m: (m.order, m.name),
    )
    out: list[_Injection] = []
    for spec in specs:
        out.extend(renderer(spec, feature_key))
    return tuple(out)
