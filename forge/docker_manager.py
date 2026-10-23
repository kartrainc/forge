"""Docker Compose rendering and lifecycle management."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from jinja2 import Environment, FileSystemLoader

from forge.config import (
    DEFAULT_REALM,
    TRAEFIK_DASHBOARD_PORT,
    BackendLanguage,
    FrontendFramework,
    ProjectConfig,
)
from forge.config._topology import (
    backend_topology_entry,
    compute_render_postgres,
    compute_rendered_infra,
)
from forge.errors import GeneratorError
from forge.services.registry import get_services_for_capabilities

if TYPE_CHECKING:
    from forge.capability_resolver import ResolvedPlan
    from forge.synthesis import PlatformSynthesis

TEMPLATES_DIR = Path(__file__).parent / "templates"

BUILD_DIR = {
    FrontendFramework.VUE: "dist",
    FrontendFramework.SVELTE: "build",
}


# -- Rendering ----------------------------------------------------------------


def _jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_compose(
    config: ProjectConfig,
    project_root: Path,
    plan: ResolvedPlan | None = None,
    synthesis: PlatformSynthesis | None = None,
) -> Path:
    """Render docker-compose.yml into the project root.

    When ``plan`` is supplied, its capabilities are resolved against
    ``forge.services.SERVICE_REGISTRY`` and the matched templates are
    emitted as additional top-level services in the compose file.

    When ``synthesis`` is supplied (Phase 4 multi-service synthesis is
    active), each backend's per-service S2S / inter-service env block
    (``GATEKEEPER_CLIENT_ID``/``_SECRET``/``_TOKEN_ENDPOINT``,
    ``INTERNAL_SERVICE_URL_*``, optional ``APP__EVENTS__BUS_URL``) is
    injected into that service's ``environment:`` block. When ``None``
    (the default / single-service case) every backend's ``synthesis_env``
    is an empty dict and the template emits zero extra bytes — preserving
    the golden byte-identity contract.
    """
    env = _jinja_env()
    template = env.get_template("deploy/docker-compose.yml.j2")

    extra_services: list[dict[str, object]] = []
    extra_volumes: list[str] = []
    if plan is not None:
        seen_volumes: set[str] = set()
        # ``plan.capabilities`` is a ``frozenset`` — iteration order is
        # hash-based and non-deterministic across runs. Sort to keep
        # the rendered docker-compose.yml stable so golden snapshots
        # don't drift between identical generations.
        for svc in get_services_for_capabilities(sorted(plan.capabilities)):
            block = svc.as_compose_dict()
            if svc.name == "gatekeeper":
                block.setdefault("environment", {})["API_KEYS_ENABLED"] = (
                    "true" if plan.option_values.get("auth.api_keys") else "false"
                )
            extra_services.append({"name": svc.name, "block": block})
            for vol in svc.named_volumes:
                if vol not in seen_volumes:
                    seen_volumes.add(vol)
                    extra_volumes.append(vol)

    has_frontend = (
        config.frontend is not None and config.frontend.framework != FrontendFramework.NONE
    )

    # Whether a project-root ``packages/`` tree actually ships — only the
    # platform-auth SDK fragments create it. forge-core is backend-local
    # (services/<svc>/sdks/forge-core, main build context), so a no-auth
    # project has no project-root ``packages/``. Declaring the ``packages`` build
    # context when the dir is absent makes ``docker compose build`` fail
    # ("failed to get build context packages") — so gate the context on this.
    active_fragments = {rf.fragment.name for rf in plan.ordered} if plan is not None else set()
    has_project_sdks = bool(
        active_fragments
        & {
            "platform_auth_sdk_python",
            "platform_auth_sdk_node",
            "platform_auth_sdk_rust",
        }
    )

    # Build per-backend context list for the template loop. The per-backend
    # entry shape and the postgres predicate live in ``forge.config._topology``
    # so this compose render and the topology-aware Helm chart can't drift —
    # ``backend_topology_entry`` is the single source of truth for a backend's
    # name/port/db/migrations/deps/S2S, ``compute_render_postgres`` for whether
    # a Postgres service is in the stack.

    # Phase B1: ``database_mode=none`` suppresses the postgres container and
    # per-backend migrate sidecars. Keycloak has its own DB needs, so the
    # postgres service still renders when it's enabled.
    database_mode = config.database_mode
    render_postgres = compute_render_postgres(
        has_backends=bool(config.backends),
        database_mode=database_mode,
        include_keycloak=config.include_keycloak,
    )

    # Infra services the compose will actually render. A backend only gets a
    # ``depends_on`` edge to a service in this set — declaring a dependency on
    # an unrendered service makes ``docker compose up`` fail with "depends on
    # undefined service". ``redis``/``keycloak`` render only under
    # ``include_keycloak``; capability services (cache/queue/etc.) come in via
    # ``extra_services``.
    rendered_infra: set[str] = compute_rendered_infra(
        config,
        render_postgres=render_postgres,
        extra_service_names=tuple(str(svc["name"]) for svc in extra_services),
    )

    backends_ctx = [backend_topology_entry(bc, synthesis, rendered_infra) for bc in config.backends]

    # Primary backend (first) for backward-compat references
    primary = config.backend

    context = {
        "project_slug": config.project_slug,
        "backends": backends_ctx,
        "has_project_sdks": has_project_sdks,
        "backend_language": primary.language.value if primary else "python",
        "backend_slug": config.backend_slug,
        "backend_port": primary.server_port if primary else 5000,
        "db_name": config.backend_slug.replace("-", "_"),
        "has_frontend": has_frontend,
        "frontend_slug": config.frontend_slug if has_frontend else "",
        "frontend_port": (
            config.frontend.server_port if config.frontend and has_frontend else 5173
        ),
        "include_keycloak": config.include_keycloak,
        "keycloak_port": config.keycloak_port,
        "traefik_dashboard_port": TRAEFIK_DASHBOARD_PORT,
        "database_mode": database_mode,
        "render_postgres": render_postgres,
        "keycloak_realm": (
            config.frontend.keycloak_realm
            if config.frontend
            and config.include_keycloak
            and config.frontend.keycloak_realm != "master"
            else DEFAULT_REALM
        ),
        "keycloak_client_id": (
            config.frontend.keycloak_client_id
            if config.frontend and config.include_keycloak
            else config.frontend_slug
        ),
        "extra_services": extra_services,
        "extra_volumes": extra_volumes,
    }

    output = template.render(context)
    compose_path = project_root / "docker-compose.yml"
    compose_path.write_text(output, encoding="utf-8")
    return compose_path


# -- Workspace root manifests -------------------------------------------------
#
# Generated projects are flat monorepos (apps/, services/, packages/). With
# cross-package source deps (``file:../packages/<name>`` for Node,
# ``path = "../packages/<name>"`` for Rust), they need a workspace root so:
#
#   - the in-tree SDK (e.g. ``packages/platform-auth-node``) builds before
#     its consumers run ``tsc --noEmit`` against missing ``dist/``
#     artifacts (matrix-verify Node was tripping on this), and
#   - the Rust SDK path-dep resolves at the workspace root rather than
#     against the leaf service dir whose context Docker can't escape
#     (matrix-verify + smoke Rust were tripping on this).
#
# Renders are conditional: emit ``package.json`` only when the project
# has a Node consumer (Node service, Vue/Svelte frontend, or the Node
# SDK fragment); emit ``Cargo.toml`` only when the project has a Rust
# consumer (Rust service or the Rust SDK fragment).


def _project_has_node(config: ProjectConfig, active_fragments: set[str]) -> bool:
    if any(b.language == BackendLanguage.NODE for b in config.backends):
        return True
    if config.frontend is not None and config.frontend.framework in (
        FrontendFramework.VUE,
        FrontendFramework.SVELTE,
    ):
        return True
    # A node-based plugin frontend (e.g. Solid/Qwik) also needs the npm
    # workspace root — its FrontendSpec declares node_based.
    if config.frontend is not None:
        from forge.config import FRONTEND_SPECS  # noqa: PLC0415

        spec = FRONTEND_SPECS.get(config.frontend.framework.value)
        if spec is not None and spec.node_based:
            return True
    return "platform_auth_sdk_node" in active_fragments


def _project_has_rust(config: ProjectConfig, active_fragments: set[str]) -> bool:
    if any(b.language == BackendLanguage.RUST for b in config.backends):
        return True
    return "platform_auth_sdk_rust" in active_fragments


def render_workspace_package_json(
    config: ProjectConfig,
    project_root: Path,
    plan: ResolvedPlan | None = None,
) -> Path | None:
    """Render the workspace-root ``package.json`` for npm workspaces.

    Returns the written path, or ``None`` when the project has no Node
    consumer (Python+Flutter-only scenarios skip this).
    """
    active = {rf.fragment.name for rf in plan.ordered} if plan is not None else set()
    if not _project_has_node(config, active):
        return None
    env = _jinja_env()
    template = env.get_template("deploy/package.json.j2")
    output = template.render(
        {
            "project_slug": config.project_slug,
            "project_name": config.project_name,
        }
    )
    pkg_path = project_root / "package.json"
    pkg_path.write_text(output, encoding="utf-8")
    return pkg_path


def render_workspace_cargo_toml(
    config: ProjectConfig,
    project_root: Path,
    plan: ResolvedPlan | None = None,
) -> Path | None:
    """Render the workspace-root ``Cargo.toml`` for Cargo workspaces.

    Returns the written path, or ``None`` when the project has no Rust
    consumer.
    """
    active = {rf.fragment.name for rf in plan.ordered} if plan is not None else set()
    if not _project_has_rust(config, active):
        return None

    rust_backends = [
        {"name": b.name} for b in config.backends if b.language == BackendLanguage.RUST
    ]
    # The Rust SDK ships at ``packages/platform-auth-rs/`` per the fragment's
    # files/ tree. Only listing one SDK today; new Rust SDKs would append
    # to this list when their fragments enter the plan.
    rust_sdk_members: list[str] = []
    if "platform_auth_sdk_rust" in active:
        rust_sdk_members.append("packages/platform-auth-rs")

    # Pick a workspace edition: the first Rust backend's edition wins. If
    # there are no Rust backends but the SDK is present (unusual but
    # legal), fall back to the SDK's edition.
    edition = "2024"
    for b in config.backends:
        if b.language == BackendLanguage.RUST:
            edition = b.rust_edition or "2024"
            break

    env = _jinja_env()
    template = env.get_template("deploy/Cargo.toml.j2")
    output = template.render(
        {
            "project_name": config.project_name,
            "rust_backends": rust_backends,
            "rust_sdk_members": rust_sdk_members,
            "rust_edition": edition,
        }
    )
    cargo_path = project_root / "Cargo.toml"
    cargo_path.write_text(output, encoding="utf-8")
    return cargo_path


def render_frontend_dockerfile(config: ProjectConfig, frontend_dir: Path) -> Path:
    """Render a two-stage production Dockerfile into the frontend directory."""
    env = _jinja_env()
    fc = config.frontend
    if fc is None:
        raise ValueError("render_frontend_dockerfile called without a frontend configured")

    if fc.framework == FrontendFramework.FLUTTER:
        template = env.get_template("deploy/Dockerfile.flutter.j2")
        context: dict[str, object] = {}
    else:
        # Built-in node frontends key build_dir off BUILD_DIR; a plugin
        # frontend declares it (+ package_manager) on its FrontendSpec.
        from forge.config import FRONTEND_SPECS  # noqa: PLC0415

        spec = FRONTEND_SPECS.get(fc.framework.value)
        build_dir = spec.build_dir if spec is not None else BUILD_DIR.get(fc.framework, "dist")
        template = env.get_template("deploy/Dockerfile.node.j2")
        context = {
            "package_manager": fc.package_manager,
            "build_dir": build_dir,
        }

    output = template.render(context)
    dockerfile_path = frontend_dir / "Dockerfile"
    dockerfile_path.write_text(output, encoding="utf-8")
    return dockerfile_path


def render_keycloak_realm(
    config: ProjectConfig,
    project_root: Path,
    synthesis: PlatformSynthesis | None = None,
) -> Path:
    """Render keycloak-realm.json into the project root.

    The rendered JSON is parsed before being written so a Jinja typo or quoting bug
    fails generation immediately rather than producing a realm Keycloak will reject
    at boot. A few essential keys are checked too — these catch the common
    template-edit mistake of dropping a top-level field.

    When ``synthesis`` is supplied (Phase 4 multi-service synthesis active),
    one confidential ``svc-<name>`` realm client per service is appended to the
    realm's ``clients[]`` array so each backend can mint S2S tokens via the
    client-credentials grant. When ``None`` the appended list is empty and the
    realm JSON is byte-identical to the single-service output.
    """
    import json

    env = _jinja_env()
    template = env.get_template("infra/keycloak-realm.json.j2")

    fc = config.frontend
    # Phase 4 service clients — one per synthesized ServiceClient. The template
    # guards on an empty list so single-service output is unchanged.
    service_clients = (
        [{"client_id": client.client_id, "secret": client.secret} for client in synthesis.clients]
        if synthesis
        else []
    )
    context = {
        "project_name": config.project_name,
        "keycloak_realm": (
            fc.keycloak_realm
            if fc and fc.keycloak_realm and fc.keycloak_realm != "master"
            else DEFAULT_REALM
        ),
        "keycloak_client_id": (
            fc.keycloak_client_id if fc and fc.keycloak_client_id else config.project_slug
        ),
        "service_clients": service_clients,
    }

    output = template.render(context)
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError as e:
        raise GeneratorError(
            f"Rendered Keycloak realm JSON is invalid (line {e.lineno} col {e.colno}): {e.msg}. "
            "Check forge/templates/infra/keycloak-realm.json.j2 for an unbalanced quote, "
            "trailing comma, or unrendered Jinja expression."
        ) from e
    for required in ("realm", "clients"):
        if required not in parsed:
            raise GeneratorError(
                f"Rendered Keycloak realm JSON is missing required top-level key '{required}'."
            )
    infra_dir = project_root / "deploy" / "infra"
    infra_dir.mkdir(parents=True, exist_ok=True)
    realm_path = infra_dir / "keycloak-realm.json"
    realm_path.write_text(output, encoding="utf-8")
    return realm_path


def render_init_db(
    config: ProjectConfig,
    project_root: Path,
    synthesis: PlatformSynthesis | None = None,
) -> Path:
    """Render init-db.sh that creates databases for all backends + keycloak.

    When ``synthesis`` selects the ``postgres_notify`` event bus, the shared
    event-bus database (``events``) is provisioned alongside the per-backend
    and keycloak databases. When ``None`` (or any non-postgres bus) the set of
    databases is unchanged — byte-identical to the pre-synthesis output.
    """
    env = _jinja_env()
    template = env.get_template("deploy/init-db.sh.j2")

    # One db per backend plus keycloak. The template guards each CREATE with
    # ``WHERE NOT EXISTS`` so listing the primary (already created by
    # ``POSTGRES_DB`` env var) is idempotent — and multi-backend users expect
    # every service's database to be visible here.
    #
    # Phase B1: backend DBs are skipped when ``database.mode=none`` — the
    # backends are stateless in that mode. Keycloak's own ``keycloak`` db
    # still gets created because keycloak always needs its own store.
    extra_dbs = set()
    if config.database_mode != "none":
        for bc in config.backends:
            db_name = bc.name.replace("-", "_")
            extra_dbs.add(db_name)
    if config.include_keycloak:
        extra_dbs.add("keycloak")
    # Phase 4: the postgres LISTEN/NOTIFY event bus needs its own shared db.
    if synthesis and synthesis.event_bus == "postgres_notify" and synthesis.event_bus_db:
        extra_dbs.add(synthesis.event_bus_db)

    output = template.render({"extra_databases": sorted(extra_dbs)})
    # Deployment/orchestration support files live under deploy/ to keep the
    # project root focused on app code; the compose volume mount points here.
    init_path = project_root / "deploy" / "compose" / "init-db.sh"
    init_path.parent.mkdir(parents=True, exist_ok=True)
    # Write with LF line endings (CRLF breaks shebang in Linux containers)
    init_path.write_bytes(output.replace("\r\n", "\n").encode("utf-8"))
    # Executable, so the Postgres image runs it by its shebang. Its entrypoint
    # executes a ``*.sh`` init script when ``[ -x ]`` holds and sources it
    # otherwise; Docker Desktop's file sharing reports a bind-mounted 0644 file
    # as executable yet refuses to execute it (exit 126), so no database but
    # POSTGRES_DB was created there. On Windows, chmod only clears read-only.
    init_path.chmod(0o755)
    return init_path


def render_service_registry(
    config: ProjectConfig,
    synthesis: PlatformSynthesis | None,
    project_root: Path,
) -> Path | None:
    """Render the gatekeeper S2S service registry, or no-op when inactive.

    Phase 4 (P4.2). When ``synthesis`` is ``None`` (single-service / feature
    off) this is a no-op and returns ``None`` — the baseline
    ``service_registry.yaml`` shipped by the ``platform_auth_gatekeeper``
    fragment is left untouched, preserving the golden byte-identity contract.

    When synthesis is active this REPLACES
    ``<project_root>/infra/gatekeeper/secrets/service_registry.yaml`` (the
    fragment's baseline ships placeholder ``svc-*`` entries that are bogus
    for a forge project) with a registry conforming to the vendored
    ``ServiceRegistry`` schema: one entry per :class:`ServiceClient` carrying

    * ``client_id`` — the OIDC client id (``svc-<name>``);
    * ``secret_hash`` — an argon2id digest of the deterministic dev plaintext
      (the plaintext itself lives only in the caller's compose
      ``GATEKEEPER_CLIENT_SECRET`` env var, never in the registry);
    * ``k8s_subject`` — ``system:serviceaccount:<project_slug>:<name>``;
    * ``audiences`` — ``<callee_client_id>: {scopes: [sorted...]}`` from the
      client's depends_on-derived audience grants;
    * ``may_act_for_audiences`` — empty (no RFC-8693 token-exchange wiring in
      forge-synthesized graphs yet).

    The output is byte-stable across renders: clients keep config order,
    audiences are sorted, and each argon2 salt is derived from the project slug
    and the client id. A random salt made every re-render differ, so an
    ownership-managed ``forge --update`` saw an upstream change on each run and
    a project that had customised the registry could never finish an update.
    A derived salt costs nothing here: the plaintext is a deterministic
    development secret documented in the header, and production rotates the
    hashes or uses the ``k8s_subject`` verifier. The caller's plaintext still
    verifies against the hash (the in-process coherence test proves this).
    """
    if synthesis is None:
        return None

    # Lazy import — argon2 cost is paid only when synthesis is active.
    import yaml  # noqa: PLC0415
    from argon2 import PasswordHasher  # noqa: PLC0415
    from argon2.low_level import hash_secret  # noqa: PLC0415

    hasher = PasswordHasher()

    def _dev_secret_hash(client_id: str, secret: str) -> str:
        seed = f"forge-dev-s2s:{config.project_slug}:{client_id}".encode()
        salt = hashlib.sha256(seed).digest()[:16]
        return hash_secret(
            secret.encode(),
            salt,
            time_cost=hasher.time_cost,
            memory_cost=hasher.memory_cost,
            parallelism=hasher.parallelism,
            hash_len=hasher.hash_len,
            type=hasher.type,
        ).decode()

    services: list[dict[str, object]] = []
    for client in synthesis.clients:
        audiences: dict[str, dict[str, list[str]]] = {}
        for callee_client_id in sorted(client.audiences):
            audiences[callee_client_id] = {"scopes": sorted(client.audiences[callee_client_id])}
        services.append(
            {
                "client_id": client.client_id,
                "secret_hash": _dev_secret_hash(client.client_id, client.secret),
                "k8s_subject": (f"system:serviceaccount:{config.project_slug}:{client.name}"),
                "audiences": audiences,
                "may_act_for_audiences": [],
            }
        )

    # Loud DEV-ONLY header. The per-service plaintext is documented here for
    # local onboarding only (it also lives in each caller's compose
    # GATEKEEPER_CLIENT_SECRET) — the registry body below stores only the
    # argon2id hash. Mirror the baseline fragment file's documented-secrets
    # style so the contract is recognisable.
    secret_lines = "\n".join(
        f"#   {client.client_id:<16} → {client.secret}" for client in synthesis.clients
    )
    header = (
        "# DEV-ONLY synthesized S2S secrets — rotate before any non-local deployment.\n"
        "#\n"
        "# Service-to-service client registry for the gatekeeper /auth/token endpoint.\n"
        "# Generated by forge multi-service platform synthesis\n"
        "# (auth.service_discovery=true). Each entry is one calling service;\n"
        "# ``secret_hash`` is an argon2id hash of the dev pre-shared secret. The\n"
        "# calling service stores the PLAINTEXT in its own GATEKEEPER_CLIENT_SECRET\n"
        "# env var (see docker-compose.yml); the registry never stores plaintext.\n"
        "#\n"
        "# DEV SECRETS DOCUMENTED HERE FOR ONBOARDING:\n"
        f"{secret_lines}\n"
        "#\n"
        "# These are NEVER acceptable in production. Production deployments use the\n"
        "# k8s ProjectedSAToken verifier (k8s_subject below) or rotate the hashes.\n"
    )
    body = yaml.safe_dump(
        {"services": services},
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )
    output = f"{header}\n{body}"

    registry_path = (
        project_root / "deploy" / "infra" / "gatekeeper" / "secrets" / "service_registry.yaml"
    )
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(output, encoding="utf-8")
    return registry_path


def render_nginx_conf(config: ProjectConfig, frontend_dir: Path) -> Path:
    """Render nginx.conf into the frontend directory.

    It serves the static build with an SPA fallback and, under ``security.csp``,
    the headers and nonce policy of :mod:`forge.frontend_csp`.
    """
    from forge import frontend_csp  # noqa: PLC0415

    env = _jinja_env()
    template = env.get_template("deploy/nginx.conf.j2")
    output = template.render(
        {
            "security_headers": frontend_csp.security_headers(config),
            "content_security_policy": frontend_csp.content_security_policy(config),
            "nonce_placeholder": frontend_csp.NONCE_PLACEHOLDER,
            "nonce_variable": frontend_csp.NONCE_VARIABLE,
        }
    )
    nginx_path = frontend_dir / "nginx.conf"
    nginx_path.write_text(output, encoding="utf-8")
    return nginx_path


# -- Lifecycle ----------------------------------------------------------------


def _docker_running() -> bool:
    """Check if the Docker daemon is reachable."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def boot(project_root: Path) -> None:
    """Run docker compose up --build with error handling."""
    compose_file = project_root / "docker-compose.yml"
    if not compose_file.exists():
        print("  Error: docker-compose.yml not found.")
        return

    if not _docker_running():
        print("  Error: Docker is not running.")
        print("  Please start Docker Desktop and try again:")
        print(f"    cd {project_root}")
        print("    docker compose up --build")
        return

    print("  Starting Docker Compose stack ...")
    print("  (Press Ctrl+C to stop)\n")
    try:
        subprocess.run(
            ["docker", "compose", "up", "--build"],
            cwd=str(project_root),
            check=True,
        )
    except subprocess.CalledProcessError:
        print("\n  Docker Compose failed. Cleaning up ...")
        teardown(project_root)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n\n  Interrupted. Cleaning up ...")
        teardown(project_root)


def teardown(project_root: Path) -> None:
    """Run docker compose down to clean up containers."""
    subprocess.run(
        ["docker", "compose", "down", "--volumes", "--remove-orphans"],
        cwd=str(project_root),
        capture_output=True,
    )
    print("  Stack stopped and cleaned up.")
