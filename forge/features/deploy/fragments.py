"""Kubernetes/Helm deployment fragments.

``deploy.target=kubernetes`` realises a single fragment:

  ``deploy_helm_chart`` — a TOPOLOGY-AWARE Helm umbrella chart at
  ``<project>/deploy/helm/``. Project-scoped and language-agnostic. Its
  ``values.yaml`` is rendered at generate/update time from the project's
  deployment topology (:func:`forge.config._topology.compute_topology`,
  exposed to the fragment as the ``topology`` Jinja variable), producing one
  ``workloads`` entry per backend plus the frontend and platform-service
  toggles. The chart's ``templates/*.yaml`` are pure Go-templates copied
  verbatim — they ``range`` over ``.Values.workloads`` and resolve at
  ``helm install`` time.

Raw per-backend k8s manifests are no longer hand-authored: ``deploy/k8s/`` is
derived on demand from the chart (``helm template`` — see the generated
``Makefile``), so the manifests can never drift from the chart.
"""

from __future__ import annotations

from pathlib import Path

from forge.api import ForgeAPI
from forge.config import BackendLanguage
from forge.fragments._spec import Fragment, FragmentImplSpec

_TEMPLATES = Path(__file__).resolve().parent / "templates"


def _impl(name: str, lang: str) -> str:
    return str(_TEMPLATES / name / lang)


def register_all(api: ForgeAPI) -> None:
    # Project-scoped, topology-aware Helm chart. Lands at
    # ``<project>/deploy/helm/``; ``values.yaml`` is rendered from the
    # deployment topology, the Go-template chart bodies are verbatim.
    api.add_fragment(
        Fragment(
            name="deploy_helm_chart",
            implementations={
                BackendLanguage.PYTHON: FragmentImplSpec(
                    fragment_dir=_impl("deploy_helm_chart", "all"),
                    scope="project",
                ),
                BackendLanguage.NODE: FragmentImplSpec(
                    fragment_dir=_impl("deploy_helm_chart", "all"),
                    scope="project",
                ),
                BackendLanguage.RUST: FragmentImplSpec(
                    fragment_dir=_impl("deploy_helm_chart", "all"),
                    scope="project",
                ),
                BackendLanguage.GO: FragmentImplSpec(
                    fragment_dir=_impl("deploy_helm_chart", "all"),
                    scope="project",
                ),
            },
        )
    )
    # deploy.helm_layout=per_service: one chart per backend (deploy/helm/<backend>/) and per frontend
    # (deploy/helm/<frontend>/), the ``__backend__``/``__frontend__`` directories of its tree expanded per topology
    # entry, plus deploy/helm/platform/. The resolver puts it in place of deploy_helm_chart.
    api.add_fragment(
        Fragment(
            name="deploy_helm_service_charts",
            implementations={
                lang: FragmentImplSpec(
                    fragment_dir=_impl("deploy_helm_service_charts", "all"), scope="project"
                )
                for lang in (
                    BackendLanguage.PYTHON,
                    BackendLanguage.NODE,
                    BackendLanguage.RUST,
                    BackendLanguage.GO,
                )
            },
        )
    )
