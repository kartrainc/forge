"""Tests for forge.docker_manager compose rendering."""

import fnmatch
from pathlib import PurePosixPath

import yaml

from forge.config import (
    BackendConfig,
    FrontendConfig,
    FrontendFramework,
    ProjectConfig,
)
from forge.docker_manager import (
    render_compose,
    render_frontend_dockerfile,
    render_frontend_dockerignore,
    render_keycloak_realm,
    render_nginx_conf,
)


def _make_config(
    framework=FrontendFramework.VUE,
    include_keycloak=False,
    has_frontend=True,
):
    bc = BackendConfig(project_name="Test App", server_port=5000)
    fc = None
    if has_frontend:
        fc = FrontendConfig(
            framework=framework,
            project_name="Test App",
            server_port=5173,
            keycloak_url="http://localhost:8080",
            keycloak_realm="master",
            keycloak_client_id="test-app",
        )
    return ProjectConfig(
        project_name="Test App",
        backends=[bc],
        frontends=([fc] if fc is not None else []),
        include_keycloak=include_keycloak,
        keycloak_port=8080,
    )


class TestRenderCompose:
    def test_backend_only(self, tmp_path):
        config = _make_config(has_frontend=False)
        compose_path = render_compose(config, tmp_path)
        data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))

        assert "backend" in data["services"]
        assert "postgres" in data["services"]
        assert "frontend" not in data["services"]

    def test_backend_with_vue(self, tmp_path):
        config = _make_config(framework=FrontendFramework.VUE)
        compose_path = render_compose(config, tmp_path)
        data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))

        assert "frontend" in data["services"]

    def test_flutter_included_in_compose(self, tmp_path):
        config = _make_config(framework=FrontendFramework.FLUTTER)
        compose_path = render_compose(config, tmp_path)
        data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))

        assert "frontend" in data["services"]

    def test_frontend_no_vite_env_vars(self, tmp_path):
        config = _make_config(framework=FrontendFramework.VUE)
        compose_path = render_compose(config, tmp_path)
        data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))

        frontend = data["services"]["frontend"]
        assert "environment" not in frontend

    def test_keycloak_included(self, tmp_path):
        config = _make_config(include_keycloak=True)
        compose_path = render_compose(config, tmp_path)
        data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))

        assert "keycloak" in data["services"]
        assert data["services"]["keycloak"]["ports"] == ["8080:8080"]
        env = data["services"]["backend"]["environment"]
        assert env["APP__SECURITY__AUTH__ENABLED"] == "true"

    def test_keycloak_excluded(self, tmp_path):
        config = _make_config(include_keycloak=False)
        compose_path = render_compose(config, tmp_path)
        data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))

        assert "keycloak" not in data["services"]
        env = data["services"]["backend"]["environment"]
        assert env["APP__SECURITY__AUTH__ENABLED"] == "false"

    def test_pgadmin_in_tools_profile(self, tmp_path):
        config = _make_config(has_frontend=False)
        compose_path = render_compose(config, tmp_path)
        data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))

        assert data["services"]["pgadmin"]["profiles"] == ["tools"]

    def test_network_defined(self, tmp_path):
        config = _make_config(has_frontend=False)
        compose_path = render_compose(config, tmp_path)
        data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))

        assert "app-network" in data["networks"]

    def test_backend_has_traefik_labels(self, tmp_path):
        config = _make_config(has_frontend=False)
        compose_path = render_compose(config, tmp_path)
        data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))

        labels = data["services"]["backend"]["labels"]
        assert any("traefik.enable=true" in l for l in labels)
        assert any("/api/backend" in l for l in labels)

    def test_traefik_always_present(self, tmp_path):
        config = _make_config(has_frontend=False)
        compose_path = render_compose(config, tmp_path)
        data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))

        assert "traefik" in data["services"]

    def test_postgres_healthcheck(self, tmp_path):
        config = _make_config(has_frontend=False)
        compose_path = render_compose(config, tmp_path)
        data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))

        pg = data["services"]["postgres"]
        assert "healthcheck" in pg
        assert pg["healthcheck"]["retries"] == 5


def _several_frontends(include_keycloak=False):
    def vue(name, port):
        return FrontendConfig(
            framework=FrontendFramework.VUE, project_name="Test App", name=name, server_port=port
        )

    return ProjectConfig(
        project_name="Test App",
        backends=[BackendConfig(name="api", project_name="Test App", server_port=5010)],
        frontends=[vue("frontend", 5173), vue("admin", 5174)],
        include_keycloak=include_keycloak,
    )


class _UniqueKeyLoader(yaml.SafeLoader):
    """``yaml.safe_load`` keeps the last of two equal keys; this refuses them."""


def _no_duplicates(loader, node, deep=False):
    keys = [loader.construct_object(key, deep=deep) for key, _ in node.value]
    assert len(keys) == len(set(keys)), f"duplicate keys: {keys}"
    return loader.construct_mapping(node, deep)


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicates)


class TestSeveralFrontends:
    """One compose service and router per frontend; the primary's stay as they are."""

    def _render(self, tmp_path, config, plan=None):
        return yaml.load(  # noqa: S506 — a SafeLoader subclass
            render_compose(config, tmp_path, plan).read_text(encoding="utf-8"),
            Loader=_UniqueKeyLoader,
        )

    def test_each_frontend_has_its_service_and_host(self, tmp_path):
        services = self._render(tmp_path, _several_frontends())["services"]
        assert services["frontend"]["build"]["context"] == "./apps/frontend"
        assert services["admin"]["build"]["context"] == "./apps/admin"
        assert services["admin"]["volumes"] == [
            "./apps/admin/nginx.conf:/etc/nginx/conf.d/default.conf:ro"
        ]
        assert (
            "traefik.http.routers.frontend.rule=Host(`app.localhost`)"
            in (services["frontend"]["labels"])
        )
        assert (
            "traefik.http.routers.admin.rule=Host(`admin.localhost`)"
            in (services["admin"]["labels"])
        )
        assert services["admin"]["depends_on"] == ["api"]

    def test_a_flutter_app_keeps_its_folder_and_takes_a_dns_service_name(self, tmp_path):
        config = _several_frontends()
        config.frontends[1] = FrontendConfig(
            framework=FrontendFramework.FLUTTER,
            project_name="Test App",
            name="frontend_1",
            server_port=5174,
            include_openapi=True,
        )
        services = self._render(tmp_path, config)["services"]
        assert "frontend_1" not in services
        assert services["frontend-1"]["build"]["context"] == "./apps/frontend_1"
        assert (
            "traefik.http.routers.frontend-1.rule=Host(`frontend-1.localhost`)"
            in (services["frontend-1"]["labels"])
        )

    def test_backend_routes_answer_on_every_frontend_host(self, tmp_path):
        labels = self._render(tmp_path, _several_frontends())["services"]["api"]["labels"]
        assert (
            "traefik.http.routers.api.rule=(Host(`app.localhost`) || Host(`admin.localhost`))"
            " && PathPrefix(`/api/api`)"
        ) in labels

    def test_one_frontend_keeps_the_single_host_rule(self, tmp_path):
        labels = self._render(tmp_path, _make_config())["services"]["backend"]["labels"]
        assert (
            "traefik.http.routers.backend.rule=Host(`app.localhost`) && PathPrefix(`/api/backend`)"
        ) in labels

    def test_the_gatekeeper_learns_the_extra_host_labels(self):
        # ``generate`` loads the features that register the gatekeeper service.
        from forge.generator import generate

        several = _several_frontends(include_keycloak=True)
        single = _make_config(include_keycloak=True)
        single.backend.server_port = 5010  # the gatekeeper's port is 5000
        for config, expected in ((several, "admin"), (single, None)):
            config.validate()
            root = generate(config, quiet=True, dry_run=True)
            compose = yaml.load(  # noqa: S506 — a SafeLoader subclass
                (root / "docker-compose.yml").read_text(encoding="utf-8"),
                Loader=_UniqueKeyLoader,
            )
            env = compose["services"]["gatekeeper"]["environment"]
            assert env.get("GATEKEEPER_FRONTEND_HOST_LABELS") == expected


class TestRenderFrontendDockerfile:
    def test_two_stage_build(self, tmp_path):
        config = _make_config(framework=FrontendFramework.VUE)
        path = render_frontend_dockerfile(config, tmp_path)
        content = path.read_text(encoding="utf-8")

        assert "FROM node:22-slim AS builder" in content
        assert "FROM nginx:alpine" in content
        assert "run build" in content

    def test_vue_copies_dist(self, tmp_path):
        config = _make_config(framework=FrontendFramework.VUE)
        path = render_frontend_dockerfile(config, tmp_path)
        content = path.read_text(encoding="utf-8")

        assert "/app/dist" in content

    def test_svelte_copies_build(self, tmp_path):
        config = _make_config(framework=FrontendFramework.SVELTE)
        path = render_frontend_dockerfile(config, tmp_path)
        content = path.read_text(encoding="utf-8")

        assert "/app/build" in content

    def test_pnpm_dockerfile(self, tmp_path):
        config = _make_config(framework=FrontendFramework.SVELTE)
        config.frontend.package_manager = "pnpm"
        path = render_frontend_dockerfile(config, tmp_path)
        content = path.read_text(encoding="utf-8")

        assert "corepack enable" in content
        assert "pnpm-lock.yaml" in content

    def test_bun_dockerfile(self, tmp_path):
        config = _make_config(framework=FrontendFramework.SVELTE)
        config.frontend.package_manager = "bun"
        path = render_frontend_dockerfile(config, tmp_path)
        content = path.read_text(encoding="utf-8")

        assert "npm install -g bun" in content
        assert "bun.lockb" in content

    def test_flutter_dockerfile(self, tmp_path):
        config = _make_config(framework=FrontendFramework.FLUTTER)
        path = render_frontend_dockerfile(config, tmp_path)
        content = path.read_text(encoding="utf-8")

        assert "cirruslabs/flutter" in content
        assert "flutter build web" in content
        assert "FROM nginx:alpine" in content
        assert "build/web" in content

    def test_nginx_conf_copied(self, tmp_path):
        """Dockerfile references nginx.conf for all frameworks."""
        for fw in (FrontendFramework.VUE, FrontendFramework.SVELTE, FrontendFramework.FLUTTER):
            config = _make_config(framework=fw)
            path = render_frontend_dockerfile(config, tmp_path)
            content = path.read_text(encoding="utf-8")
            assert "nginx.conf" in content


def _ignore_patterns(path):
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def _ignored(path, patterns):
    """Whether a context path, or a directory above it, matches an ignore pattern.

    ``fnmatch`` lets ``*`` cross ``/``, which Docker's matcher does not, so this
    reports at least everything Docker would leave out: a path this says is
    kept is kept.
    """
    parts = PurePosixPath(path).parts
    prefixes = [PurePosixPath(*parts[:i]).as_posix() for i in range(1, len(parts) + 1)]
    return any(fnmatch.fnmatchcase(prefix, pattern) for prefix in prefixes for pattern in patterns)


class TestRenderFrontendDockerignore:
    def test_node_context_leaves_out_the_host_install_builds_and_local_env(self, tmp_path):
        config = _make_config(framework=FrontendFramework.VUE)
        path = render_frontend_dockerignore(config, tmp_path)

        assert path == tmp_path / ".dockerignore"
        patterns = _ignore_patterns(path)
        for left_out in (
            "node_modules/vite/index.js",
            "dist/index.html",
            "build/index.html",
            ".svelte-kit/tsconfig.json",
            "coverage/lcov.info",
            ".env.local",
            ".env.production.local",
        ):
            assert _ignored(left_out, patterns), left_out

    def test_node_context_keeps_what_the_image_builds_from(self, tmp_path):
        config = _make_config(framework=FrontendFramework.SVELTE)
        patterns = _ignore_patterns(render_frontend_dockerignore(config, tmp_path))

        for kept in (
            "package.json",
            "package-lock.json",
            ".env",
            ".env.development",
            "index.html",
            "vite.config.ts",
            "src/app/main.ts",
            "public/favicon.ico",
        ):
            assert not _ignored(kept, patterns), kept

    def test_flutter_context_leaves_out_the_build_tool_cache_and_plugin_manifests(self, tmp_path):
        config = _make_config(framework=FrontendFramework.FLUTTER)
        patterns = _ignore_patterns(render_frontend_dockerignore(config, tmp_path))

        for left_out in (
            "build/web/index.html",
            ".dart_tool/package_config.json",
            ".flutter-plugins",
            ".flutter-plugins-dependencies",
            ".packages",
        ):
            assert _ignored(left_out, patterns), left_out
        for kept in ("pubspec.yaml", "pubspec.lock", "lib/main.dart", "build.yaml"):
            assert not _ignored(kept, patterns), kept


class TestRenderNginxConf:
    def test_static_only_no_api_proxy(self, tmp_path):
        config = _make_config()
        path = render_nginx_conf(config, tmp_path)
        content = path.read_text(encoding="utf-8")

        assert "try_files" in content
        assert "proxy_pass" not in content

    def test_spa_fallback(self, tmp_path):
        config = _make_config()
        path = render_nginx_conf(config, tmp_path)
        content = path.read_text(encoding="utf-8")

        assert "/index.html" in content


class TestKeycloakRealmTenantClaim:
    """Regression guard for the generated realm's tenant_id wiring.

    The gatekeeper ``/callback`` persists ``tenant_id`` on self-registered
    users via the Keycloak Admin API. For that to work end-to-end the realm
    must (1) declare ``tenant_id`` in the declarative user-profile with an
    ``ADMIN_EDIT`` unmanaged policy (else Keycloak silently drops the write),
    (2) grant the gatekeeper service account ``manage-users``/``view-users``
    (else the Admin call 403s), and (3) emit the tenant claim under the same
    namespace forge-core + the gatekeeper read (``https://forge/tenant_id``).
    See gatekeeper ``config.py`` (``tenant_id_claim``) + ``routes.py``
    (``set_user_attribute``).
    """

    # The forge ecosystem default: forge-core DEFAULT_TENANT_ID_CLAIM, the
    # Node/Rust SDKs, the in_memory issuer, and the gatekeeper compose env
    # (TENANT_ID_CLAIM) all read this namespace, so the realm mapper must
    # emit the same one or the gatekeeper can't read the user token's tenant.
    EXPECTED_CLAIM = "https://forge/tenant_id"

    def _realm(self, tmp_path):
        import json

        config = _make_config(include_keycloak=True)
        path = render_keycloak_realm(config, tmp_path)
        return json.loads(path.read_text(encoding="utf-8"))

    def test_tenant_claim_namespace_matches_gatekeeper(self, tmp_path):
        import json

        realm = self._realm(tmp_path)
        claim_names = [
            m["config"]["claim.name"]
            for client in realm["clients"]
            for m in client.get("protocolMappers", [])
            if m["config"].get("user.attribute") == "tenant_id"
        ]
        # Both the SPA and gatekeeper clients carry the tenant_id mapper.
        assert claim_names, "no tenant_id protocol mapper found"
        assert all(c == self.EXPECTED_CLAIM for c in claim_names), claim_names
        # Realm must use a single, consistent namespace (no stray platform/).
        assert "https://platform/tenant_id" not in json.dumps(realm)

    def test_userprofile_declares_tenant_id(self, tmp_path):
        import json

        realm = self._realm(tmp_path)
        providers = realm["components"]["org.keycloak.userprofile.UserProfileProvider"]
        cfg = providers[0]["config"]["kc.user.profile.config"][0]
        attrs = json.loads(cfg)
        names = {a["name"] for a in attrs["attributes"]}
        assert "tenant_id" in names
        assert attrs["unmanagedAttributePolicy"] == "ADMIN_EDIT"

    def test_gatekeeper_service_account_can_manage_users(self, tmp_path):
        realm = self._realm(tmp_path)
        sa = next(
            (u for u in realm["users"] if u.get("username") == "service-account-gatekeeper"),
            None,
        )
        assert sa is not None, "service-account-gatekeeper user missing"
        assert sa["serviceAccountClientId"] == "gatekeeper"
        assert sa["clientRoles"]["realm-management"] == ["manage-users", "view-users"]
