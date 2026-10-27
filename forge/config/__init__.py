"""Configuration types + registries for forge.

P1.1 (Epic 1c) split the legacy ``forge/config.py`` (824 lines) into
namespaced submodules:

* ``_validators`` — port + feature validators, Keycloak/Traefik
  constants. Zero deps; foundation for the others.
* ``_backend`` — :class:`BackendLanguage`, :class:`BackendSpec`,
  :class:`BackendConfig`, :data:`BACKEND_REGISTRY`, plugin sentinel
  + register/resolve helpers.
* ``_frontend`` — :class:`FrontendFramework`, :class:`FrontendSpec`,
  :class:`FrontendConfig`, :data:`FRONTEND_SPECS`,
  :data:`FRONTEND_RESERVED`, plugin sentinel + helpers.
* ``_project`` — :class:`ProjectConfig` + the ``_validate_*`` family.

Importing this package re-exports every name the rest of forge (and
plugin authors) used from the legacy module — no caller needs to
update its imports.
"""

from __future__ import annotations

from forge.config._backend import (
    BACKEND_REGISTRY,
    PLUGIN_LANGUAGES,
    BackendConfig,
    BackendLanguage,
    BackendSpec,
    _PluginLanguage,
    available_backend_languages,
    register_backend_language,
    resolve_backend_language,
)
from forge.config._frontend import (
    FRONTEND_RESERVED,
    FRONTEND_SPECS,
    PLUGIN_FRAMEWORKS,
    PRIMARY_FRONTEND_NAME,
    PRIMARY_HOST_LABEL,
    PRIMARY_LOCAL_HOST,
    FrontendConfig,
    FrontendFramework,
    FrontendSpec,
    _PluginFramework,
    available_frontend_frameworks,
    frontend_host_label,
    frontend_local_host,
    frontend_uses_subdirectory,
    register_frontend_framework,
    resolve_frontend_framework,
)
from forge.config._project import ProjectConfig
from forge.config._validators import (
    DEFAULT_REALM,
    TRAEFIK_DASHBOARD_PORT,
    keycloak_client_id_from,
    validate_features,
    validate_port,
    validate_slug,
)

__all__ = [
    # Constants
    "BACKEND_REGISTRY",
    "DEFAULT_REALM",
    "FRONTEND_RESERVED",
    "FRONTEND_SPECS",
    "PRIMARY_FRONTEND_NAME",
    "PRIMARY_HOST_LABEL",
    "PRIMARY_LOCAL_HOST",
    "PLUGIN_FRAMEWORKS",
    "PLUGIN_LANGUAGES",
    "TRAEFIK_DASHBOARD_PORT",
    # Backend
    "BackendConfig",
    "BackendLanguage",
    "BackendSpec",
    "_PluginLanguage",
    "available_backend_languages",
    "register_backend_language",
    "resolve_backend_language",
    # Frontend
    "FrontendConfig",
    "FrontendFramework",
    "FrontendSpec",
    "_PluginFramework",
    "available_frontend_frameworks",
    "frontend_host_label",
    "frontend_local_host",
    "frontend_uses_subdirectory",
    "register_frontend_framework",
    "resolve_frontend_framework",
    # Project
    "ProjectConfig",
    # Validators / helpers
    "keycloak_client_id_from",
    "validate_features",
    "validate_port",
    "validate_slug",
]
