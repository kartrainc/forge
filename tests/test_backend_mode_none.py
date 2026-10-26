"""Phase A — ``backend.mode=none`` scenario tests.

Exercises the discriminated-union discriminator introduced in Phase A of
the forge configuration modernization (see the improvement plan at
``.claude/plans/role-expertise-you-snappy-scroll.md``). The scenario
covered here is a frontend-only project pointed at an externally-hosted
API — something forge couldn't express before Phase A because at least
one backend was required and the frontend's API URL was always inferred
from the first backend's port.

Three layers of coverage:

* **Config validation** — ``ProjectConfig.validate`` enforces coherence
  between ``backend.mode`` and the ``backends`` list / ``frontend``
  config / ``frontend.api_target_url`` option.
* **Variable mapping** — ``variable_mapper._frontend_api_urls`` returns
  the external URL for all three consumers (``api_base_url``,
  ``api_proxy_target``, ``env_api_base_url``) when in external mode.
* **Docker compose rendering** — a frontend-only compose omits
  ``postgres`` / ``pgadmin`` and lacks ``depends_on`` on the frontend
  service.

The end-to-end generation test is marked ``e2e`` because it runs Copier
and writes to disk; unit-level tests run in the default suite.
"""

from __future__ import annotations

import pytest
import yaml

from forge import variable_mapper
from forge.config import (
    BackendConfig,
    BackendLanguage,
    FrontendConfig,
    FrontendFramework,
    ProjectConfig,
)
from forge.docker_manager import render_compose

# -- Config validation -------------------------------------------------------


def _frontend_only_config(api_target_url: str = "https://api.example.com") -> ProjectConfig:
    """Return a minimal frontend-only project pointed at an external API.

    ``features=["items"]`` satisfies the Svelte template's ``features``
    validator (it requires at least one feature) without coupling the
    test to specific UI routes — the generated pages are scaffolding
    the external API can populate or ignore.
    """
    fc = FrontendConfig(
        framework=FrontendFramework.SVELTE,
        project_name="External Consumer",
        features=["items"],
        server_port=5173,
        include_auth=False,
        include_openapi=False,
        keycloak_url="http://localhost:8080",
        keycloak_realm="master",
        keycloak_client_id="external-consumer",
    )
    return ProjectConfig(
        project_name="External Consumer",
        backends=[],
        frontend=fc,
        options={
            "backend.mode": "none",
            "frontend.api_target_url": api_target_url,
        },
    )


class TestLayerModeValidation:
    def test_frontend_only_with_external_url_validates(self):
        config = _frontend_only_config()
        config.validate()
        assert config.backend_mode == "none"
        assert config.frontend_api_target_url == "https://api.example.com"

    def test_mode_none_with_backends_rejected(self):
        bc = BackendConfig(project_name="stray", server_port=5000)
        config = ProjectConfig(
            project_name="Conflict",
            backends=[bc],
            frontend=None,
            options={"backend.mode": "none"},
        )
        with pytest.raises(ValueError, match=r"backend\.mode=none.*incompatible"):
            config.validate()

    def test_mode_none_with_frontend_but_no_url_rejected(self):
        config = _frontend_only_config(api_target_url="")
        # B2 renamed the canonical path to ``frontend.api_target.url``;
        # the error message now uses it. The dot-separated form matches
        # either the old or new path prefix, keeping the assertion
        # forward-compatible.
        with pytest.raises(ValueError, match=r"frontend\.api_target"):
            config.validate()

    def test_empty_project_rejected(self):
        config = ProjectConfig(project_name="Empty", backends=[], frontend=None)
        with pytest.raises(ValueError, match=r"Empty project"):
            config.validate()

    def test_default_mode_is_generate(self):
        bc = BackendConfig(project_name="default-be", server_port=5000)
        config = ProjectConfig(project_name="Default", backends=[bc])
        config.validate()
        assert config.backend_mode == "generate"
        assert config.frontend_api_target_url == ""


# -- Variable mapper ---------------------------------------------------------


class TestFrontendApiUrls:
    def test_external_mode_returns_external_url_for_all_three(self):
        config = _frontend_only_config()
        config.validate()
        api, proxy, env = variable_mapper._frontend_api_urls(
            config, backend_name="backend", backend_port=5000
        )
        assert api == "https://api.example.com"
        assert proxy == "https://api.example.com"
        assert env == "https://api.example.com"

    def test_local_mode_preserves_historical_behavior(self):
        bc = BackendConfig(project_name="api", server_port=5000)
        fc = FrontendConfig(
            framework=FrontendFramework.SVELTE,
            project_name="local",
            features=["items"],
            server_port=5173,
            include_auth=False,
            include_openapi=False,
            keycloak_url="http://localhost:8080",
            keycloak_realm="master",
            keycloak_client_id="local",
        )
        config = ProjectConfig(project_name="Local", backends=[bc], frontend=fc)
        config.validate()
        api, proxy, env = variable_mapper._frontend_api_urls(
            config, backend_name="api", backend_port=5000
        )
        assert api == "http://localhost:5000"
        assert proxy == "http://api:5000"
        assert env == "http://localhost:5173"

    def test_svelte_context_carries_external_url(self):
        config = _frontend_only_config()
        config.validate()
        ctx = variable_mapper.svelte_context(config)
        assert ctx["api_base_url"] == "https://api.example.com"
        assert ctx["api_proxy_target"] == "https://api.example.com"
        assert ctx["env_api_base_url"] == "https://api.example.com"


# -- Compose rendering -------------------------------------------------------


class TestComposeFrontendOnly:
    def test_no_backend_services(self, tmp_path):
        config = _frontend_only_config()
        config.validate()
        compose_path = render_compose(config, tmp_path)
        data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
        services = data["services"]
        assert "frontend" in services
        assert "traefik" in services
        assert "postgres" not in services
        assert "pgadmin" not in services

    def test_frontend_has_no_depends_on(self, tmp_path):
        """Empty ``backends`` means the frontend service has no upstream
        to wait for; the compose template must omit the key rather than
        emit an empty list (which docker-compose rejects)."""
        config = _frontend_only_config()
        config.validate()
        compose_path = render_compose(config, tmp_path)
        data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
        assert "depends_on" not in data["services"]["frontend"]

    def test_no_pgdata_volume(self, tmp_path):
        config = _frontend_only_config()
        config.validate()
        compose_path = render_compose(config, tmp_path)
        data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
        # ``volumes`` may be entirely absent or present as an empty mapping
        # depending on whether any ``{% if %}`` block emitted it. Neither
        # ``pgdata`` nor ``redis_data`` should appear.
        vols = data.get("volumes") or {}
        assert "pgdata" not in vols
        assert "redis_data" not in vols


# -- Generator gating (no Copier invocation) --------------------------------


def test_generate_skips_backend_loop_and_renders_compose(tmp_path, monkeypatch):
    """Exercise ``generate()`` with ``backend.mode=none`` + Svelte.

    The Svelte template's post-generate task runs ``npm install`` + a
    production build, which would make this test minutes-long and
    environment-dependent. Stub ``_run_copier`` to a no-op so we verify
    the generator-level flow (compose rendering, forge.toml stamping,
    no ``services/`` creation) without paying for Copier + npm.

    Validates:
      * No per-backend loop iteration (no ``services/`` directory).
      * ``docker-compose.yml`` renders with frontend + traefik, no
        postgres / pgadmin / backend-migrate services.
      * The compose was produced by the broadened condition in
        ``generator.generate`` (not the old ``if config.backends`` gate).
    """
    from forge import generator

    # _run_copier is what invokes Copier for every template. Stubbing it
    # keeps the frontend Copier run (with its post_generate.py npm install)
    # out of the test while still letting the rest of generate() execute.
    monkeypatch.setattr(generator, "_run_copier", lambda *a, **kw: None)

    config = _frontend_only_config(api_target_url="https://jsonplaceholder.typicode.com")
    config.output_dir = str(tmp_path)
    config.validate()

    project_root = generator.generate(config, quiet=True, dry_run=True)

    assert not (project_root / "services").exists(), (
        "backend.mode=none should skip services/ generation entirely"
    )

    compose_path = project_root / "docker-compose.yml"
    assert compose_path.exists(), (
        "compose must render for frontend-only projects — Phase A broadened "
        "the generator's condition beyond `if config.backends`"
    )
    data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    services = data["services"]
    assert "frontend" in services
    assert "traefik" in services
    assert "postgres" not in services
    assert "pgadmin" not in services
    assert all(not name.endswith("-migrate") for name in services)


# -- Defensive: mode=none with a lingering explicit backend language --------


def test_mode_none_ignores_backendlanguage_sentinels():
    """``backend.mode=none`` must not crash when someone passes a
    ``BackendLanguage`` default into ``BackendConfig`` fields while
    leaving ``backends`` empty — the discriminator is what gates
    generation, not residual dataclass defaults.
    """
    fc = FrontendConfig(
        framework=FrontendFramework.VUE,
        project_name="Guard",
        features=["items"],
        server_port=5173,
        include_auth=False,
        include_openapi=False,
        keycloak_url="http://localhost:8080",
        keycloak_realm="master",
        keycloak_client_id="guard",
    )
    config = ProjectConfig(
        project_name="Guard",
        backends=[],
        frontend=fc,
        options={
            "backend.mode": "none",
            "frontend.api_target_url": "https://api.example.com",
        },
    )
    config.validate()
    assert config.backend_mode == "none"
    # Sanity: resolving the default options doesn't accidentally add a
    # BackendLanguage.PYTHON backend.
    assert config.backends == []
    # Property-level consistency.
    assert isinstance(BackendLanguage.PYTHON, BackendLanguage)


# -- CLI builder path: backends: [] -----------------------------------------


def _empty_namespace():
    """Argparse namespace with every CLI flag the builder reads set to ``None``.

    Mirrors the helper at ``tests/matrix/runner.py::_project_config_from_dict``
    so config-file values win uniformly — exactly what a
    ``forge --config foo.yaml`` invocation produces.
    """
    import argparse  # noqa: PLC0415

    ns = argparse.Namespace()
    for attr in (
        "project_name",
        "frontend",
        "yes",
        "quiet",
        "json_output",
        "no_docker",
        "backend_port",
        "python_version",
        "features",
        "description",
        "set_options",
        "backend_language",
        "backend_name",
        "include_auth",
        "include_chat",
        "include_openapi",
        "frontend_port",
        "color_scheme",
        "author_name",
        "package_manager",
        "org_name",
        "api_base_url",
        "api_proxy_target",
        "generate_e2e_tests",
        "include_keycloak",
        "keycloak_port",
        "keycloak_realm",
        "keycloak_url",
        "node_version",
        "rust_edition",
        "output_dir",
    ):
        setattr(ns, attr, None)
    return ns


def test_builder_honors_explicit_empty_backends_list():
    """Regression: YAML ``backends: []`` must yield a frontend-only project.

    Before WS1, ``_build_backends_from_cfg`` used
    ``if isinstance(backends_raw, list) and backends_raw:`` — an explicit
    empty list was falsy and indistinguishable from a missing key. The
    builder synthesised a placeholder Python backend, then
    ``ProjectConfig.validate`` rejected the config with
    "backend.mode=none is incompatible with 1 configured backend(s)".
    """
    from forge.cli.builder import _build_config  # noqa: PLC0415

    cfg = {
        "project_name": "Frontend Only Vue",
        "backends": [],
        "frontend": {
            "framework": "vue",
            "include_auth": False,
            "server_port": 5190,
            "api_base_url": "http://localhost:9999",
        },
        "options": {
            "backend.mode": "none",
            "frontend.api_target.url": "http://localhost:9999",
        },
    }
    config = _build_config(_empty_namespace(), cfg)
    config.validate()
    assert config.backends == []
    assert config.backend_mode == "none"
    assert config.frontend is not None
    assert config.frontend.framework == FrontendFramework.VUE
    assert config.frontend.api_base_url == "http://localhost:9999"


def test_builder_pipes_api_proxy_target_through_frontend_cfg():
    """Regression: ``frontend.api_proxy_target`` in YAML must reach FrontendConfig.

    Companion to ``api_base_url`` — both are real ``FrontendConfig``
    dataclass fields (see ``forge/config/_frontend.py:171-172``) that the
    pre-WS1 builder silently dropped from the cfg dict.
    """
    from forge.cli.builder import _build_config  # noqa: PLC0415

    cfg = {
        "project_name": "Frontend Only Vue",
        "backends": [],
        "frontend": {
            "framework": "vue",
            "include_auth": False,
            "server_port": 5190,
            "api_base_url": "http://localhost:9999",
            "api_proxy_target": "http://localhost:9999",
        },
        "options": {
            "backend.mode": "none",
            "frontend.api_target.url": "http://localhost:9999",
        },
    }
    config = _build_config(_empty_namespace(), cfg)
    config.validate()
    assert config.frontend is not None
    assert config.frontend.api_base_url == "http://localhost:9999"
    assert config.frontend.api_proxy_target == "http://localhost:9999"
