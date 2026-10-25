"""Backwards-compatible re-export shim for the legacy import path.

The :class:`MiddlewareSpec` dataclass + per-backend renderers moved to
:mod:`forge.specs.middleware` in 1.3.0 (Pillar A.2) as the first
:class:`~forge.appliers.renderers.FragmentRenderer` implementation. This
shim keeps existing plugin imports (``from forge.middleware_spec import
MiddlewareSpec``) working.

TODO: remove in 2.0.
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
