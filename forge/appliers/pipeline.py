"""Orchestrator composing the four appliers in the canonical order.

``FragmentPipeline.default()`` reproduces the pre-Epic-A
``_apply_fragment`` behaviour: build a :class:`FragmentPlan`, run
files → injection → deps → env in that order. The order matters —
inject.yaml can reference files a fragment just copied, and
dependency files (``pyproject.toml``, ``package.json``) are
themselves injection targets, so deps must run after injection.

Swap an applier by constructing a pipeline with your own instance:

    pipeline = FragmentPipeline(
        files=FragmentFileApplier(),
        injection=MyMiddlewareInjectionApplier(),
        deps=FragmentDepsApplier(),
        env=FragmentEnvApplier(),
    )
    pipeline.run(ctx, impl, feature_key)

Epic K uses this swap for its ``MiddlewareSpec``-aware injection
applier that synthesises injections on the fly from a fragment's
declared middlewares. Pillar A.2 (1.3.0) generalises that shape so
every declarative spec flows through the same
:class:`~forge.appliers.renderers.FragmentRenderer` dispatch
(``MiddlewareSpec`` is the only implementer today).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from forge.appliers.deps import FragmentDepsApplier
from forge.appliers.env import FragmentEnvApplier
from forge.appliers.files import FragmentFileApplier
from forge.appliers.injection import FragmentInjectionApplier
from forge.appliers.plan import FragmentPlan

if TYPE_CHECKING:
    from forge.appliers.renderers import FragmentRenderer
    from forge.fragment_context import FragmentContext
    from forge.fragments import FragmentImplSpec
    from forge.specs.middleware import MiddlewareSpec


@dataclass(frozen=True)
class FragmentPipeline:
    """Four-applier orchestrator. Instantiate via :meth:`default` for
    the standard ordering; swap fields when extending."""

    files: FragmentFileApplier = field(default_factory=FragmentFileApplier)
    injection: FragmentInjectionApplier = field(default_factory=FragmentInjectionApplier)
    deps: FragmentDepsApplier = field(default_factory=FragmentDepsApplier)
    env: FragmentEnvApplier = field(default_factory=FragmentEnvApplier)

    @classmethod
    def default(cls) -> FragmentPipeline:
        """Factory for the standard pipeline."""
        return cls()

    def run(
        self,
        ctx: FragmentContext,
        impl: FragmentImplSpec,
        feature_key: str,
        *,
        renderers: tuple[FragmentRenderer, ...] = (),
        middlewares: tuple[MiddlewareSpec, ...] = (),
        shared_env_vars: tuple[tuple[str, str], ...] = (),
    ) -> None:
        """Build the plan + apply each phase in the canonical order.

        ``renderers`` (Pillar A.2, 1.3.0) are
        :class:`~forge.appliers.renderers.FragmentRenderer` instances —
        :class:`MiddlewareSpec` today, plus any future declarative spec
        type. Each is expanded into injections at plan-build time via its
        own :meth:`~FragmentRenderer.render`.

        ``middlewares`` (Epic K) is preserved for one release as a
        compatibility shim — :meth:`FragmentPlan.from_impl` folds the
        legacy tuple into ``renderers`` transparently.

        ``shared_env_vars`` (``Fragment.shared_env_vars``) is merged
        with ``impl.env_vars`` before the env applier runs, so per-
        backend impls don't have to repeat backend-agnostic env vars
        (``AWS_REGION``, ``S3_ENDPOINT_URL``, …). Empty tuple is the
        default and preserves the pre-Fragment-DX shape.
        """
        plan = FragmentPlan.from_impl(
            impl,
            feature_key,
            options=ctx.options,
            renderers=renderers,
            middlewares=middlewares,
            backend=ctx.backend_config.language,
            shared_env_vars=shared_env_vars,
            ui_kit=ctx.ui_kit,
        )
        self.files.apply(ctx, plan)
        self.injection.apply(ctx, plan)
        self.deps.apply(ctx, plan)
        self.env.apply(ctx, plan)
