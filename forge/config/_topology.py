"""Deployment topology — the project model shared by compose and Helm.

``docker-compose.yml.j2`` has always been topology-aware: it loops over the
real backends, knows the frontend, and conditionally renders postgres/redis/
keycloak. The Helm chart needs that *same* model so it can emit one
Deployment/Service/HPA per backend (plus the frontend and optional in-cluster
infra) and stay current on ``forge --update``.

This module extracts the topology shape into one place so the two consumers —
:func:`forge.docker_manager.render_compose` and the topology-aware
``deploy_helm_chart`` fragment — cannot drift. The per-backend entry builder
(:func:`backend_topology_entry`) and the postgres predicate
(:func:`compute_render_postgres`) are imported by ``docker_manager`` so the
compose render and the topology dict are guaranteed identical for the keys
they share.

:func:`compute_topology` returns a plain ``dict`` (not a dataclass) so a
fragment ``.jinja`` template can walk it with either attribute or item access
(``topology.backends`` / ``be.name``) under Jinja's getattr→getitem fallback.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from forge.config._backend import BackendLanguage
from forge.config._frontend import PRIMARY_LOCAL_HOST, FrontendFramework
from forge.config._validators import DEFAULT_REALM

if TYPE_CHECKING:
    from forge.capability_resolver import ResolvedPlan
    from forge.config._backend import BackendConfig
    from forge.config._frontend import FrontendConfig
    from forge.config._project import ProjectConfig
    from forge.synthesis.platform import PlatformSynthesis

# Languages that ship a DB migration step (and thus a ``<svc>-migrate``
# sidecar in compose / a migrate Job in Helm). Mirrors the ``_BUILTIN_MIGRATING``
# set ``docker_manager`` used inline before this extraction.
BUILTIN_MIGRATING: frozenset[BackendLanguage] = frozenset(
    {BackendLanguage.PYTHON, BackendLanguage.NODE, BackendLanguage.RUST, BackendLanguage.GO}
)


def compute_render_postgres(
    *, has_backends: bool, database_mode: str, include_keycloak: bool
) -> bool:
    """Whether a Postgres service belongs in the stack.

    Postgres renders when the project has backends with a real database, OR
    when Keycloak is enabled (it needs its own database regardless of
    ``database.mode``). Shared by ``docker_manager.render_compose`` and
    :func:`compute_topology` so the compose stack and the Helm chart agree.
    """
    return (has_backends and database_mode != "none") or include_keycloak


def backend_topology_entry(
    bc: BackendConfig,
    synthesis: PlatformSynthesis | None,
    rendered_infra: set[str],
) -> dict[str, Any]:
    """Build one backend's topology entry (name/port/db/migrations/deps/S2S).

    ``rendered_infra`` is the set of infra service names that are actually in
    the stack; an app-template's ``requires_services`` edge is only carried
    through when its target is rendered (declaring a dependency on an
    unrendered service would make ``docker compose up`` — and a Helm
    ``initContainer`` wait — fail).
    """
    from forge.backend_app_templates import (  # noqa: PLC0415 — avoid config<->app_templates cycle
        DEFAULT_BACKEND_TEMPLATE,
        get_backend_application_template,
    )

    app_tmpl = get_backend_application_template(
        bc.language, bc.app_template or DEFAULT_BACKEND_TEMPLATE
    )
    depends_on_services = [
        svc for svc in (app_tmpl.requires_services if app_tmpl else ()) if svc in rendered_infra
    ]
    return {
        "name": bc.name,
        "language": bc.language.value,
        "port": bc.server_port,
        "db_name": bc.name.replace("-", "_"),
        "has_migrations": bc.language in BUILTIN_MIGRATING,
        "depends_on_services": depends_on_services,
        # Per-service S2S / inter-service env block; empty when synthesis is
        # inactive (single-service or auth.service_discovery off).
        "synthesis_env": synthesis.env_for(bc.name) if synthesis else {},
    }


def _extra_service_names(plan: ResolvedPlan | None) -> tuple[str, ...]:
    """Capability-contributed service names (qdrant, etc.) in the stack."""
    if plan is None:
        return ()
    from forge.services.registry import get_services_for_capabilities  # noqa: PLC0415

    return tuple(svc.name for svc in get_services_for_capabilities(sorted(plan.capabilities)))


def compute_rendered_infra(
    config: ProjectConfig,
    *,
    render_postgres: bool,
    extra_service_names: tuple[str, ...] = (),
) -> set[str]:
    """Infra service names actually rendered into the stack.

    Mirrors ``docker_manager.render_compose``: capability services always
    render; ``redis``/``keycloak`` only under ``include_keycloak``; ``postgres``
    only when :func:`compute_render_postgres` says so.
    """
    rendered: set[str] = set(extra_service_names)
    if config.include_keycloak:
        rendered |= {"keycloak", "redis"}
    if render_postgres:
        rendered.add("postgres")
    return rendered


def frontend_topology_entry(config: ProjectConfig, fc: FrontendConfig) -> dict[str, Any]:
    """One non-primary frontend as compose and Helm render it.

    ``name`` is its ``apps/<name>`` folder; ``dns_name`` names its compose
    service, Traefik router and Helm resources; ``host`` is its compose host.
    """
    from forge import frontend_csp  # noqa: PLC0415 — imports forge.config

    return {
        "name": fc.name,
        "dns_name": fc.dns_name,
        "host": config.frontend_host(fc),
        "port": fc.server_port,
        "security_headers": frontend_csp.security_headers(config, fc),
        "csp": frontend_csp.content_security_policy(config, fc),
    }


def frontend_host_rule(config: ProjectConfig) -> str:
    """The Traefik host rule backend routers match: every frontend's host.

    One frontend (or none) keeps the rule every project has always had.
    """
    hosts = [config.frontend_host(fc) for fc in config.active_frontends]
    if len(hosts) <= 1:
        return f"Host(`{PRIMARY_LOCAL_HOST}`)"
    return "(" + " || ".join(f"Host(`{host}`)" for host in hosts) + ")"


def compute_topology(
    config: ProjectConfig,
    plan: ResolvedPlan | None = None,
    *,
    synthesis: PlatformSynthesis | None = None,
) -> dict[str, Any]:
    """Build the deployment topology dict for compose and Helm.

    The returned dict is the canonical project model: the backend list (each
    with name/language/port/db_name/has_migrations/depends_on_services/
    synthesis_env), the frontend presence + slug, and the platform-service
    flags (``include_keycloak``, ``render_postgres``, ``database_mode``).
    """
    database_mode = config.database_mode
    render_postgres = compute_render_postgres(
        has_backends=bool(config.backends),
        database_mode=database_mode,
        include_keycloak=config.include_keycloak,
    )
    rendered_infra = compute_rendered_infra(
        config,
        render_postgres=render_postgres,
        extra_service_names=_extra_service_names(plan),
    )
    backends = tuple(
        backend_topology_entry(bc, synthesis, rendered_infra) for bc in config.backends
    )
    # Frontends beside the primary; the primary keeps the scalar keys below.
    extra_frontends = tuple(
        frontend_topology_entry(config, fc)
        for fc in config.active_frontends
        if not config.is_primary_frontend(fc)
    )
    has_frontend = (
        config.frontend is not None and config.frontend.framework != FrontendFramework.NONE
    )
    from forge import frontend_csp  # noqa: PLC0415 — imports forge.config

    return {
        "project_slug": config.project_slug,
        "backends": backends,
        "has_frontend": has_frontend,
        "frontend_slug": config.frontend_slug if has_frontend else "",
        "frontend_port": (
            config.frontend.server_port if config.frontend and has_frontend else 5173
        ),
        "include_keycloak": config.include_keycloak,
        "keycloak_port": config.keycloak_port,
        # Realm + client_id sourced EXACTLY as docker_manager builds the compose
        # render context, so the Helm auth env (values.yaml.jinja) and compose
        # agree. Empty/default when not a keycloak project.
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
        "database_mode": database_mode,
        "render_postgres": render_postgres,
        # security.csp: what the frontend's nginx sends (forge.frontend_csp);
        # empty when the option is off or the frontend is not the Vue template.
        "frontend_security_headers": frontend_csp.security_headers(config),
        "frontend_csp": frontend_csp.content_security_policy(config),
        "extra_frontends": extra_frontends,
    }
