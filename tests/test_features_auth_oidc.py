"""Invariants for the ``oidc_generic`` auth provider fragment.

``auth.provider=oidc_generic`` points the service's issuer-agnostic platform-
auth ``AuthGuard`` at *any* external OIDC issuer (Keycloak direct / Auth0 /
Cognito / Okta / Azure AD) via OIDC discovery + JWKS — NO Gatekeeper container,
no Keycloak realm, no Redis. The issuer is env-driven (``AUTH_PROVIDER_*``).

This file gates:
  - the fragment's registration + shape (Python-only, backend-scoped, depends
    on the issuer-agnostic Python middleware);
  - the ``auth.provider`` enables wiring;
  - the provider-aware keycloak coercion (``oidc_generic`` must remain usable
    *without* ``include_keycloak`` — like ``in_memory`` — because its issuer is
    external; the coercion that forces ``auth.mode``→``none`` only fires for the
    keycloak-dependent ``gatekeeper`` provider);
  - the env-driven config + claim-mapper module: it imports cleanly (it is
    deliberately dependency-light), the dot-path claim extraction works against
    sample claim dicts from several IdP shapes, and the algorithm parsing
    accepts RS256 / ES256 / HS256 (and rejects ``none``);
  - a full dry-run render: the OIDC modules land, the guard-rebind injection
    applies, and NO gatekeeper / infra directory is generated.

Behavioural verification of the verifier crypto (a real RS256/ES256 token
verifying through the SDK ``AuthGuard``) is covered by the SDK's own parity
fixtures; the discovery + installer modules import ``httpx`` / FastAPI / the
``platform_auth`` SDK, which are runtime deps of the *generated* project (not
of forge itself), so here we gate their structure via shipped-file content.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from forge.config import (
    BackendConfig,
    BackendLanguage,
    ProjectConfig,
)
from forge.fragments import FRAGMENT_REGISTRY
from forge.generator import generate

FRAGMENT_NAME = "platform_auth_oidc_provider"

# Files the oidc fragment ADDS (it must not re-ship the middleware fragment's
# files — those land via platform_auth_python_middleware).
EXPECTED_FILES = (
    "src/app/security/__init__.py",
    "src/app/security/oidc_config.py",
    "src/app/security/oidc_discovery.py",
    "src/app/security/oidc_auth.py",
)


def _fragment_root() -> Path:
    frag = FRAGMENT_REGISTRY[FRAGMENT_NAME]
    impl = frag.implementations[BackendLanguage.PYTHON]
    return Path(impl.fragment_dir) / "files"


def _load_oidc_config() -> ModuleType:
    """Import the shipped (dependency-light) ``oidc_config`` module by path.

    The module imports only the stdlib, so it loads in forge's own
    environment — unlike the discovery / installer modules, which depend on
    the generated project's ``httpx`` / FastAPI / ``platform_auth`` runtime.
    """
    path = _fragment_root() / "src/app/security/oidc_config.py"
    spec = importlib.util.spec_from_file_location("forge_test_oidc_config", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before exec so dataclass(slots=True) annotation resolution finds
    # the module in sys.modules (CPython requires this for path-loaded modules).
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- #
# Registration + wiring
# --------------------------------------------------------------------------- #


def test_oidc_fragment_registered() -> None:
    assert FRAGMENT_NAME in FRAGMENT_REGISTRY
    frag = FRAGMENT_REGISTRY[FRAGMENT_NAME]
    # Python-only — the installer wires a FastAPI/Python application factory.
    assert BackendLanguage.PYTHON in frag.implementations
    assert BackendLanguage.NODE not in frag.implementations
    assert BackendLanguage.RUST not in frag.implementations
    # Backend-scoped — files land per Python backend.
    assert frag.implementations[BackendLanguage.PYTHON].scope == "backend"


def test_oidc_depends_on_python_middleware() -> None:
    """It imports ``AuthGuardBundle`` from the middleware fragment and rebinds
    the middleware-wired guard builder, so the middleware must be in the plan."""
    frag = FRAGMENT_REGISTRY[FRAGMENT_NAME]
    assert "platform_auth_python_middleware" in frag.depends_on


def test_oidc_wired_to_auth_provider_enables() -> None:
    from forge.options import OPTION_REGISTRY

    auth_provider = OPTION_REGISTRY["auth.provider"]
    enabled = auth_provider.enables.get("oidc_generic", ())
    assert FRAGMENT_NAME in enabled, (
        "auth.provider=oidc_generic's enables tuple must contain "
        f"{FRAGMENT_NAME!r} — without it the provider ships no verifier wiring."
    )
    # oidc_generic must NOT pull in any gatekeeper sidecar.
    assert "platform_auth_gatekeeper" not in enabled
    assert "platform_auth_gatekeeper_keygen" not in enabled


def test_oidc_pulls_no_infra_capabilities() -> None:
    """The issuer is external/env-driven — no infra is generated."""
    frag = FRAGMENT_REGISTRY[FRAGMENT_NAME]
    assert frag.capabilities == (), (
        "oidc_generic must require no infra capabilities (no redis/keycloak/etc.)"
    )


# --------------------------------------------------------------------------- #
# Shipped-file structure
# --------------------------------------------------------------------------- #


def test_oidc_files_shipped() -> None:
    root = _fragment_root()
    for relative in EXPECTED_FILES:
        path = root / relative
        assert path.is_file(), f"missing fragment file: {relative} (at {path})"


def test_oidc_does_not_reship_middleware_files() -> None:
    """Must NOT duplicate files platform_auth_python_middleware owns — a
    collision would hard-fail strict-mode generation."""
    root = _fragment_root()
    for owned in (
        "src/service/security/platform_auth_setup.py",
        "src/service/security/auth.py",
        "src/app/core/auth.py",
        "src/app/core/lifecycle.py",
    ):
        assert not (root / owned).exists(), (
            f"oidc fragment must not re-ship {owned} (owned by the "
            "middleware fragment / base template)"
        )


def test_discovery_module_prefers_discovery_with_fallback() -> None:
    """The discovery helper fetches the well-known doc and falls back to the
    Keycloak certs path only on failure."""
    src = (_fragment_root() / "src/app/security/oidc_discovery.py").read_text(encoding="utf-8")
    assert "/.well-known/openid-configuration" in src, "must use OIDC discovery"
    assert "jwks_uri" in src, "must read jwks_uri from the discovery doc"
    assert "/protocol/openid-connect/certs" in src, "must keep a Keycloak fallback"
    # Failure must DEGRADE (fall back + log), not crash boot.
    assert "except Exception" in src
    assert "fallback" in src.lower()


def test_auth_module_builds_guard_and_rebinds() -> None:
    """The installer registers the issuer+jwks_uri and constructs an AuthGuard
    with the configured algorithms + audience, then rebinds the guard builder
    (mirroring the in_memory provider's narrow rebinding seam)."""
    src = (_fragment_root() / "src/app/security/oidc_auth.py").read_text(encoding="utf-8")
    assert "def build_oidc_auth_guard" in src
    assert "def install_oidc_auth" in src
    assert "register_issuer" in src, "must register the discovered issuer+JWKS"
    assert "AuthGuard(" in src
    assert "AuthGuardBundle" in src
    assert "algorithms=settings.algorithms" in src
    # Same rebinding seam the in_memory provider uses.
    assert "_lifecycle.build_auth_guard" in src
    # The issuer is resolved via OIDC discovery, not a Gatekeeper container.
    assert "resolve_jwks_uri" in src


def test_inject_yaml_rebinds_guard_only() -> None:
    inject = (Path(_fragment_root()).parent / "inject.yaml").read_text(encoding="utf-8")
    # Rebinds the guard builder at the factory's post-configure seam (before
    # AppLifecycle.bootstrap runs).
    assert "install_oidc_auth" in inject
    assert "FORGE:APP_POST_CONFIGURE" in inject
    # OIDC has NO dev token route (unlike in_memory) — purely a verifier.
    assert "dev_auth" not in inject


# --------------------------------------------------------------------------- #
# Config + claim-mapper module (dependency-light seam)
# --------------------------------------------------------------------------- #


def test_oidc_config_module_imports() -> None:
    mod = _load_oidc_config()
    for name in (
        "OIDCSettings",
        "ClaimMapper",
        "load_oidc_settings",
        "parse_algorithms",
        "OIDCConfigError",
        "DEFAULT_ALGORITHMS",
        "DEFAULT_TENANT_CLAIM",
        "SUPPORTED_ALGORITHMS",
    ):
        assert hasattr(mod, name), f"oidc_config missing public symbol {name!r}"


def test_algorithm_parsing_rs256_es256_hs256() -> None:
    mod = _load_oidc_config()
    # Default → RS256.
    assert mod.parse_algorithms(None) == ("RS256",)
    assert mod.parse_algorithms("") == ("RS256",)
    # Each supported algorithm parses.
    assert mod.parse_algorithms("RS256") == ("RS256",)
    assert mod.parse_algorithms("ES256") == ("ES256",)
    assert mod.parse_algorithms("HS256") == ("HS256",)
    # CSV (with stray whitespace) + case-insensitivity + de-dup, order-preserving.
    assert mod.parse_algorithms("RS256, ES256 ,HS256") == ("RS256", "ES256", "HS256")
    assert mod.parse_algorithms("es256") == ("ES256",)
    assert mod.parse_algorithms("RS256,RS256") == ("RS256",)
    assert set(mod.SUPPORTED_ALGORITHMS) == {"RS256", "ES256", "HS256"}


def test_algorithm_parsing_rejects_none_and_unknown() -> None:
    mod = _load_oidc_config()
    with pytest.raises(mod.OIDCConfigError):
        mod.parse_algorithms("none")
    with pytest.raises(mod.OIDCConfigError):
        mod.parse_algorithms("NONE")
    with pytest.raises(mod.OIDCConfigError):
        mod.parse_algorithms("RS256,none")  # any 'none' poisons the set
    with pytest.raises(mod.OIDCConfigError):
        mod.parse_algorithms("PS512")  # unsupported


def test_claim_mapper_dot_path_extraction() -> None:
    mod = _load_oidc_config()
    # Flat claim (default).
    flat = mod.ClaimMapper("tenant_id")
    assert flat.tenant_id({"tenant_id": "t-flat"}) == "t-flat"
    # Nested dot-path (some Auth0 setups: organization.id).
    nested = mod.ClaimMapper("organization.id")
    assert nested.tenant_id({"organization": {"id": "org-9"}}) == "org-9"
    # Deeper nesting.
    deep = mod.ClaimMapper("a.b.c")
    assert deep.tenant_id({"a": {"b": {"c": "deep"}}}) == "deep"
    # Literal URL-shaped namespaced claim (whole-key match wins over traversal).
    url = mod.ClaimMapper("https://example.com/tenant")
    assert url.tenant_id({"https://example.com/tenant": "url-t"}) == "url-t"
    # Cognito-style 'custom:tenant' (colon, no dot — whole-key match).
    cognito = mod.ClaimMapper("custom:tenant")
    assert cognito.tenant_id({"custom:tenant": "cog-t"}) == "cog-t"
    # Missing claim → None (a verifier decision, not a mapper error).
    assert nested.tenant_id({"sub": "x"}) is None
    assert deep.tenant_id({"a": {"b": {}}}) is None
    # explicit path override.
    assert flat.extract({"org": "o1"}, path="org") == "o1"


def test_load_oidc_settings_env_driven() -> None:
    mod = _load_oidc_config()
    env = {
        "AUTH_PROVIDER_ISSUER": "https://issuer.example.com/",
        "AUTH_PROVIDER_AUDIENCE": "svc-knowledge",
        "AUTH_PROVIDER_ALGORITHMS": "ES256,RS256",
        "AUTH_PROVIDER_TENANT_CLAIM": "org.id",
    }
    s = mod.load_oidc_settings(env)
    assert s.issuer_normalised == "https://issuer.example.com"  # trailing slash stripped
    assert s.audience == "svc-knowledge"
    assert s.algorithms == ("ES256", "RS256")
    assert s.tenant_claim == "org.id"
    assert s.jwks_uri is None  # discovery, not override
    # Explicit JWKS override is threaded through.
    s2 = mod.load_oidc_settings({**env, "AUTH_PROVIDER_JWKS_URI": "https://issuer/keys"})
    assert s2.jwks_uri == "https://issuer/keys"
    # Fallbacks fill in audience / tenant_claim when the dedicated vars are unset.
    s3 = mod.load_oidc_settings(
        {"AUTH_PROVIDER_ISSUER": "https://i/"},
        fallback_audience="svc-fallback",
        fallback_tenant_claim="https://forge/tenant_id",
    )
    assert s3.audience == "svc-fallback"
    assert s3.tenant_claim == "https://forge/tenant_id"


def test_oidc_settings_requires_issuer_and_audience() -> None:
    mod = _load_oidc_config()
    with pytest.raises(mod.OIDCConfigError):
        mod.OIDCSettings(issuer="", audience="svc")
    with pytest.raises(mod.OIDCConfigError):
        mod.OIDCSettings(issuer="https://i/", audience="")


# --------------------------------------------------------------------------- #
# Resolver: provider-aware keycloak coercion
# --------------------------------------------------------------------------- #


def _py_config(provider: str, *, include_keycloak: bool) -> ProjectConfig:
    return ProjectConfig(
        project_name="oi",
        backends=[
            BackendConfig(
                name="api",
                project_name="oi",
                language=BackendLanguage.PYTHON,
                features=["items"],
            )
        ],
        frontend=None,
        include_keycloak=include_keycloak,
        options={"auth.mode": "generate", "auth.provider": provider},
    )


def test_oidc_resolves_without_keycloak() -> None:
    """oidc_generic must stay auth.mode=generate WITHOUT keycloak and pull in the
    provider fragment while EXCLUDING gatekeeper (its issuer is external)."""
    from forge.capability_resolver import resolve

    plan = resolve(_py_config("oidc_generic", include_keycloak=False))
    assert plan.option_values["auth.mode"] == "generate", (
        "oidc_generic must NOT be coerced to auth.mode=none when keycloak is off "
        "— its issuer is an external OIDC provider, no local keycloak needed"
    )
    assert plan.option_values["auth.provider"] == "oidc_generic"
    names = {rf.fragment.name for rf in plan.ordered}
    assert FRAGMENT_NAME in names
    assert "platform_auth_python_middleware" in names
    assert "platform_auth_gatekeeper" not in names
    assert "platform_auth_gatekeeper_keygen" not in names


def test_oidc_excludes_gatekeeper_even_with_keycloak() -> None:
    """Even when the project happens to enable the local keycloak stack, choosing
    oidc_generic must NOT generate the Gatekeeper container."""
    from forge.capability_resolver import resolve

    plan = resolve(_py_config("oidc_generic", include_keycloak=True))
    assert plan.option_values["auth.provider"] == "oidc_generic"
    names = {rf.fragment.name for rf in plan.ordered}
    assert FRAGMENT_NAME in names
    assert "platform_auth_gatekeeper" not in names
    assert "platform_auth_gatekeeper_keygen" not in names


# --------------------------------------------------------------------------- #
# Render: full dry-run generation
# --------------------------------------------------------------------------- #


def test_oidc_render_lands_modules_and_no_infra(tmp_path: Path) -> None:
    """Generate a Python project with auth.provider=oidc_generic and assert the
    OIDC modules land, the guard rebind injection applies, and NO gatekeeper /
    infra directory is generated."""
    cfg = ProjectConfig(
        project_name="oit",
        output_dir=str(tmp_path),
        backends=[
            BackendConfig(
                name="api",
                project_name="oit",
                language=BackendLanguage.PYTHON,
                features=["items"],
            )
        ],
        frontend=None,
        include_keycloak=False,
        options={"auth.mode": "generate", "auth.provider": "oidc_generic"},
    )
    root = Path(generate(cfg, quiet=True, dry_run=True))
    backend = root / "services" / "api"

    # OIDC modules landed.
    assert (backend / "src/app/security/oidc_config.py").is_file()
    assert (backend / "src/app/security/oidc_discovery.py").is_file()
    assert (backend / "src/app/security/oidc_auth.py").is_file()

    # NO gatekeeper container / infra dir.
    assert not (root / "deploy" / "infra" / "gatekeeper").exists(), (
        "oidc_generic must NOT generate the gatekeeper container"
    )

    # Guard-builder rebind injected into the factory BEFORE bootstrap.
    main_py = (backend / "src/app/main.py").read_text(encoding="utf-8")
    assert "install_oidc_auth(app, settings)" in main_py
    install_idx = main_py.index("install_oidc_auth(app, settings)")
    bootstrap_idx = main_py.index("AppLifecycle.bootstrap(app, settings")
    assert install_idx < bootstrap_idx, (
        "install_oidc_auth must run BEFORE AppLifecycle.bootstrap so it can "
        "rebind build_auth_guard before the guard is built"
    )

    # The middleware shim is still present (the SDK + middleware stay wired —
    # only the issuer changed).
    assert (backend / "src/service/security/platform_auth_setup.py").is_file()


@pytest.mark.parametrize("rel", EXPECTED_FILES)
def test_shipped_files_are_python_or_init(rel: str) -> None:
    """Sanity: shipped sources are non-empty .py files."""
    path = _fragment_root() / rel
    assert path.suffix == ".py"
    assert path.read_text(encoding="utf-8").strip(), f"{rel} is empty"
