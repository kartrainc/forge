"""Unit tests for the deployment topology model (``forge.config._topology``).

The topology is the project model shared by ``docker-compose.yml`` and the
topology-aware Helm chart. These tests pin its shape and the byte-identity
guard on the project-scope fragment render context.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forge.config import BackendConfig, FrontendConfig, ProjectConfig
from forge.config._backend import BackendLanguage
from forge.config._frontend import FrontendFramework
from forge.config._topology import compute_render_postgres, compute_topology


def _cfg(backends: list[BackendConfig], *, frontend=None, include_keycloak=False) -> ProjectConfig:
    return ProjectConfig(
        project_name="acme app",
        backends=backends,
        frontends=([frontend] if frontend is not None else []),
        include_keycloak=include_keycloak,
    )


# --- compute_render_postgres -------------------------------------------------


@pytest.mark.parametrize(
    ("has_backends", "database_mode", "include_keycloak", "expected"),
    [
        (True, "postgres", False, True),  # backends + real db → postgres
        (True, "none", False, False),  # backends but db disabled → no postgres
        (False, "none", True, True),  # no backends but keycloak needs its own db
        (False, "none", False, False),  # nothing needs a db
        (True, "none", True, True),  # keycloak forces postgres even with db=none
    ],
)
def test_compute_render_postgres_truth_table(
    has_backends: bool, database_mode: str, include_keycloak: bool, expected: bool
) -> None:
    assert (
        compute_render_postgres(
            has_backends=has_backends,
            database_mode=database_mode,
            include_keycloak=include_keycloak,
        )
        is expected
    )


# --- compute_topology --------------------------------------------------------


def test_topology_single_python_backend_no_frontend() -> None:
    cfg = _cfg([BackendConfig(name="api", language=BackendLanguage.PYTHON, server_port=8001)])
    topo = compute_topology(cfg)

    assert topo["project_slug"] == "acme_app"
    assert topo["has_frontend"] is False
    assert topo["include_keycloak"] is False
    assert topo["render_postgres"] is True
    assert len(topo["backends"]) == 1
    be = topo["backends"][0]
    assert be["name"] == "api"
    assert be["language"] == "python"
    assert be["port"] == 8001
    assert be["db_name"] == "api"
    assert be["has_migrations"] is True


def test_topology_multi_backend_keycloak_and_frontend() -> None:
    cfg = _cfg(
        [
            BackendConfig(name="user-api", language=BackendLanguage.PYTHON, server_port=8001),
            BackendConfig(name="billing", language=BackendLanguage.NODE, server_port=8002),
        ],
        frontend=FrontendConfig(framework=FrontendFramework.VUE, project_name="acme app"),
        include_keycloak=True,
    )
    topo = compute_topology(cfg)

    assert topo["has_frontend"] is True
    assert topo["frontend_slug"] == "frontend"
    assert topo["include_keycloak"] is True
    assert topo["render_postgres"] is True
    names = [b["name"] for b in topo["backends"]]
    assert names == ["user-api", "billing"]
    # db_name normalizes '-' -> '_'
    assert topo["backends"][0]["db_name"] == "user_api"
    assert {b["language"] for b in topo["backends"]} == {"python", "node"}


def test_topology_db_none_no_keycloak_omits_postgres() -> None:
    cfg = ProjectConfig(
        project_name="svc",
        backends=[BackendConfig(name="api", language=BackendLanguage.PYTHON)],
        options={"database.mode": "none"},
    )
    topo = compute_topology(cfg)
    assert topo["render_postgres"] is False


# --- byte-identity guard on the render context -------------------------------


def test_build_render_context_exposes_topology_only_when_set() -> None:
    from forge.appliers.files import _build_render_context
    from forge.fragment_context import FragmentContext

    proxy = BackendConfig(name="project", project_name="acme app", language=BackendLanguage.PYTHON)

    # No topology → context must NOT carry a 'topology' key (byte-identity for
    # every existing project-scope fragment).
    ctx_plain = FragmentContext.filtered(
        backend_config=proxy,
        backend_dir=Path("/tmp/x"),
        project_root=Path("/tmp/x"),
        option_values={},
        reads_options=(),
    )
    assert "topology" not in _build_render_context(ctx_plain)

    # With topology → exposed under 'topology'.
    topo = {"project_slug": "acme_app", "backends": [], "has_frontend": False}
    ctx_topo = FragmentContext.filtered(
        backend_config=proxy,
        backend_dir=Path("/tmp/x"),
        project_root=Path("/tmp/x"),
        option_values={},
        reads_options=(),
        project_topology=topo,
    )
    rendered = _build_render_context(ctx_topo)
    assert rendered["topology"] == topo
