"""``deploy.*`` options — Kubernetes/Helm deployment targets."""

from __future__ import annotations

from forge.api import ForgeAPI
from forge.options._registry import (
    FeatureCategory,
    Option,
    OptionType,
)


def register_all(api: ForgeAPI) -> None:
    api.add_option(
        Option(
            path="deploy.target",
            type=OptionType.ENUM,
            default="none",
            options=("none", "docker-compose", "kubernetes"),
            summary="Deployment target — none, docker-compose, or Kubernetes + Helm.",
            description="""\
Selects the deployment infrastructure scaffold.

- ``none`` (default): no deployment files beyond the standard generated
  ``docker-compose.yml`` forge already emits for local dev.
- ``docker-compose``: reserved for explicit compose-targeted tweaks; today
  identical to ``none`` since compose is always generated.
- ``kubernetes``: emits a TOPOLOGY-AWARE Helm umbrella chart under
  ``deploy/helm/`` — one Deployment/Service/HPA per backend plus the
  frontend, an Ingress, per-backend ConfigMap/Secret, and optional
  in-cluster datastores. The chart is rendered from the project's actual
  topology and re-rendered on ``forge --update`` so it never goes stale.

The chart's ``values.yaml`` is forge-owned (topology defaults, three-way
merged on update); copy ``values-prod.yaml.example`` to ``values-prod.yaml``
(which forge never touches) for per-environment overrides. Datastores are
EXTERNAL by default (managed Postgres/Redis/Keycloak via values); set
``infra.inCluster=true`` for throwaway in-cluster stand-ins. Raw manifests
are derived on demand via ``helm template`` (see the generated Makefile),
so ``deploy/k8s/`` can never drift from the chart.

BACKENDS: python, node, rust, go (tier 1 — the chart is language-agnostic).""",
            category=FeatureCategory.PLATFORM,
            stability="beta",
            enables={
                "kubernetes": ("deploy_helm_chart",),
                "docker-compose": (),
                "none": (),
            },
        )
    )
