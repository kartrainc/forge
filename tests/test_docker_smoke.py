"""Docker smoke tests — validate that the generated ``docker-compose.yml``
is syntactically valid YAML and contains the services each scenario is
supposed to render.

Pure-YAML structural validation (no Docker subprocess). Catches generator
regressions like a service block disappearing, a missing dependency
declaration, or a malformed Jinja interpolation. Compose-spec semantic
validation (env-var interpolation, volume references resolving, etc.)
is covered by ``matrix-nightly`` lane C, which runs ``docker compose
up --wait`` against generated projects.

The previous incarnation of this file shelled out to
``docker compose config --quiet``; on Windows hosted runners the Docker
Desktop CLI's named-pipe handshake intermittently blocked for the
full 120-second timeout. Removing the subprocess gets us cross-platform
stability for ~free.
"""

from pathlib import Path

import yaml

from forge.config import BackendConfig, FrontendConfig, FrontendFramework, ProjectConfig
from forge.generator import generate


def _assert_compose_valid(project_root: Path, *, expect_services: set[str]) -> None:
    """Parse ``docker-compose.yml`` and assert it has the expected services.

    Raises ``AssertionError`` with a detailed message on any of:
    - file missing
    - YAML syntax error (via ``yaml.safe_load``)
    - top-level not a mapping
    - one of ``expect_services`` not present under ``services:``
    """
    compose_path = project_root / "docker-compose.yml"
    assert compose_path.is_file(), f"compose file missing: {compose_path}"

    raw = compose_path.read_text(encoding="utf-8")
    doc = yaml.safe_load(raw)
    assert isinstance(doc, dict), f"compose root must be a mapping, got {type(doc).__name__}"

    services = doc.get("services") or {}
    assert isinstance(services, dict), "compose 'services' must be a mapping"

    missing = expect_services - services.keys()
    assert not missing, (
        f"compose missing expected services: {missing}. Found: {sorted(services.keys())}"
    )


class TestDockerComposeConfig:
    def test_backend_only_compose_is_valid(self, tmp_path):
        """Backend-only scenario renders ``backend`` + database services."""
        config = ProjectConfig(
            project_name="smoke-test",
            output_dir=str(tmp_path),
            backends=[BackendConfig(project_name="smoke-test", server_port=5000)],
        )
        project_root = generate(config, quiet=True)
        # ``backend`` is the BackendConfig.name default; ``backend-migrate``
        # is the alembic sidecar that runs before the api can start;
        # ``postgres`` and ``traefik`` are the always-rendered shared
        # services in this compose template.
        _assert_compose_valid(
            project_root,
            expect_services={"backend", "backend-migrate", "postgres", "traefik"},
        )

    def test_fullstack_compose_is_valid(self, tmp_path):
        """Backend + Vue frontend renders the ``frontend`` service too."""
        config = ProjectConfig(
            project_name="smoke-test",
            output_dir=str(tmp_path),
            backends=[BackendConfig(project_name="smoke-test", server_port=5000)],
            frontend=FrontendConfig(
                framework=FrontendFramework.VUE,
                project_name="smoke-test",
                server_port=5173,
                features=["items"],
            ),
        )
        project_root = generate(config, quiet=True)
        _assert_compose_valid(
            project_root,
            expect_services={"backend", "frontend", "postgres", "traefik"},
        )

    def test_keycloak_compose_is_valid(self, tmp_path):
        """Keycloak-enabled scenario renders auth-stack services."""
        config = ProjectConfig(
            project_name="smoke-test",
            output_dir=str(tmp_path),
            backends=[BackendConfig(project_name="smoke-test", server_port=5000)],
            include_keycloak=True,
            keycloak_port=8080,
        )
        project_root = generate(config, quiet=True)
        _assert_compose_valid(
            project_root,
            expect_services={"backend", "postgres", "keycloak", "gatekeeper", "redis", "traefik"},
        )


import re


class TestTraefikMiddlewareInvariant:
    """Every Traefik middleware a router references must be defined somewhere.

    When ``include_keycloak`` is set, each backend router is labelled
    ``middlewares=<be>-rewrite,auth`` but the ``auth`` ForwardAuth middleware
    has to be *defined* by a ``traefik.http.middlewares.auth.forwardauth.*``
    label. A router referencing an undefined middleware is placed in an error
    state by Traefik and serves 404 — and, worse, the intended per-request
    gatekeeper authentication never runs. This guards the reference/definition
    invariant structurally (no Docker boot required).
    """

    @staticmethod
    def _labels(svc: dict) -> list[str]:
        raw = svc.get("labels") or []
        if isinstance(raw, dict):
            return [f"{k}={v}" for k, v in raw.items()]
        return [str(x) for x in raw]

    def _referenced_and_defined(self, doc: dict) -> tuple[set[str], set[str]]:
        referenced: set[str] = set()
        defined: set[str] = set()
        for svc in (doc.get("services") or {}).values():
            for label in self._labels(svc):
                key, _, val = label.partition("=")
                if re.fullmatch(r"traefik\.http\.routers\.[^.]+\.middlewares", key):
                    referenced.update(p.strip() for p in val.split(",") if p.strip())
                m = re.match(r"traefik\.http\.middlewares\.([^.]+)\.", key)
                if m:
                    defined.add(m.group(1))
        return referenced, defined

    def test_every_referenced_middleware_is_defined(self, tmp_path):
        config = ProjectConfig(
            project_name="smoke-test",
            output_dir=str(tmp_path),
            backends=[BackendConfig(project_name="smoke-test", server_port=5010)],
            include_keycloak=True,
            keycloak_port=8080,
        )
        project_root = generate(config, quiet=True)
        doc = yaml.safe_load((project_root / "docker-compose.yml").read_text(encoding="utf-8"))
        referenced, defined = self._referenced_and_defined(doc)
        dangling = referenced - defined
        assert not dangling, (
            f"router(s) reference undefined Traefik middleware(s): {sorted(dangling)}. "
            f"defined={sorted(defined)}"
        )


class TestComposeValidatorPortParity:
    """The port-collision validator's infra reservations must match the host
    ports the compose template actually publishes — otherwise it rejects free
    ports or admits real ``docker compose up`` collisions (the two halves of
    the historical 5432-vs-15432 drift)."""

    @staticmethod
    def _host_port(entry: str) -> int:
        # "ip:host:container" | "host:container" | "container"
        parts = entry.split(":")
        if len(parts) == 3:
            return int(parts[1])
        if len(parts) == 2:
            return int(parts[0])
        return int(parts[0])

    def test_reserved_infra_ports_match_published(self, tmp_path):
        from forge.config._validators import infra_host_port_reservations

        config = ProjectConfig(
            project_name="smoke-test",
            output_dir=str(tmp_path),
            backends=[BackendConfig(project_name="smoke-test", server_port=5010)],
            frontend=FrontendConfig(
                framework=FrontendFramework.VUE,
                project_name="smoke-test",
                server_port=5173,
            ),
            include_keycloak=True,
            keycloak_port=18080,
        )
        config.validate()  # must accept this non-colliding config
        project_root = generate(config, quiet=True)
        doc = yaml.safe_load((project_root / "docker-compose.yml").read_text(encoding="utf-8"))

        app_ports = {5010, 5173}
        published: set[int] = set()
        for svc in (doc.get("services") or {}).values():
            for entry in svc.get("ports") or []:
                published.add(self._host_port(str(entry)))
        # Only host ports a backend/frontend could legally land on (>= 1024)
        # and that aren't the app services themselves.
        infra_published = {p for p in published if p >= 1024} - app_ports

        reserved = set(
            infra_host_port_reservations(
                render_postgres=True, include_keycloak=True, keycloak_port=18080
            )
        )
        assert infra_published == reserved, (
            f"validator reservations {sorted(reserved)} drift from the compose "
            f"template's published infra host ports {sorted(infra_published)}"
        )


class TestBackendAuthPointsAtGatekeeper:
    """In the gatekeeper sole-issuer model the backend validates gatekeeper's
    internal JWT (iss=gatekeeper, JWKS at <server_url>/auth/jwks, aud=the
    gatekeeper's internal-token audience). So a keycloak-enabled backend's
    APP__SECURITY__AUTH__SERVER_URL must point at gatekeeper (not Keycloak) and
    agree with GATEKEEPER_ISSUER, and its AUDIENCE must match the gatekeeper's
    INTERNAL_TOKEN_AUDIENCE. Regression guard for the server_url/audience
    mismatch that made backends reject every gatekeeper token.
    """

    def test_server_url_and_audience_match_gatekeeper(self, tmp_path):
        config = ProjectConfig(
            project_name="smoke-test",
            output_dir=str(tmp_path),
            backends=[BackendConfig(project_name="smoke-test", name="api", server_port=5010)],
            include_keycloak=True,
            keycloak_port=8080,
        )
        project_root = generate(config, quiet=True)
        doc = yaml.safe_load((project_root / "docker-compose.yml").read_text(encoding="utf-8"))
        be = doc["services"]["api"]["environment"]
        gk = doc["services"]["gatekeeper"]["environment"]

        assert be["APP__SECURITY__AUTH__SERVER_URL"] == "http://gatekeeper:5000"
        # server_url is the trusted issuer + JWKS source; it must agree with the
        # issuer gatekeeper actually stamps on the token.
        assert be["APP__SECURITY__AUTH__SERVER_URL"] == be["GATEKEEPER_ISSUER"]
        # The backend's expected audience must match what gatekeeper mints.
        assert be["APP__SECURITY__AUTH__AUDIENCE"] == gk["INTERNAL_TOKEN_AUDIENCE"]
        # The backend's forge_core auth setup derives the JWKS URI from
        # server_url + realm as the Keycloak-style
        # ``/realms/<realm>/protocol/openid-connect/certs`` path UNLESS JWKS_URI
        # is pinned. Gatekeeper serves JWKS at ``/auth/jwks``, so without this
        # pin every token verification 404s on the (non-existent) certs path.
        assert be["APP__SECURITY__AUTH__JWKS_URI"] == "http://gatekeeper:5000/auth/jwks"
        assert (
            be["APP__SECURITY__AUTH__JWKS_URI"]
            == be["APP__SECURITY__AUTH__SERVER_URL"].rstrip("/") + "/auth/jwks"
        )
