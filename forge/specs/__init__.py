"""Declarative fragment specs — :class:`~forge.appliers.renderers.FragmentRenderer`
implementations that a :class:`~forge.fragments.Fragment` ships alongside its
``inject.yaml`` to express ceremony declaratively rather than as per-backend
hand-written YAML.

The sole inhabitant today is :class:`MiddlewareSpec` (originally
``forge.middleware_spec``, Epic K). The renderer protocol is generic, so other
spec types can be added later; each must conform to the
:class:`FragmentRenderer` protocol so :meth:`FragmentPlan.from_impl` can iterate
them uniformly. (``PortSpec`` was a never-adopted seam — issue #236 solved the
shared Rust ``ports/mod.rs`` collision via ``inject.yaml`` markers instead — so
it was removed as dead weight.)
"""

from __future__ import annotations

from forge.specs.middleware import (
    MiddlewareSpec,
    render_axum_layer,
    render_chi_middleware,
    render_fastapi_middleware,
    render_fastify_plugin,
    render_middleware_injections,
)

__all__ = [
    "MiddlewareSpec",
    "render_axum_layer",
    "render_chi_middleware",
    "render_fastapi_middleware",
    "render_fastify_plugin",
    "render_middleware_injections",
]
