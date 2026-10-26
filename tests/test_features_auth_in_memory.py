"""Invariants for the ``in_memory`` auth provider fragment.

``auth.provider=in_memory`` swaps the Gatekeeper token authority for a
zero-dependency, in-process ES256 token issuer (mint + JWKS + ``/dev/auth``
route) so a developer can drive the authenticated surface of a generated
service without standing up Keycloak / Gatekeeper / Redis.

This file gates:
  - the fragment's registration + shape (Python-only, backend-scoped, depends
    on the issuer-agnostic Python middleware);
  - the ``auth.provider`` enables wiring;
  - the provider-aware keycloak coercion (``in_memory`` must remain usable
    *without* ``include_keycloak`` — the coercion that forces
    ``auth.mode``→``none`` only fires for the keycloak-dependent ``gatekeeper``
    provider);
  - a full dry-run render: the issuer + dev route land, the injections apply,
    and NO gatekeeper container is generated.

Behavioural verification of the issuer's crypto (a minted token verifying
through the SDK ``AuthGuard``) is covered by the SDK's own ``testing`` parity
fixtures; here we gate structure + the shipped module's key contents.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forge.config import (
    BackendConfig,
    BackendLanguage,
    ProjectConfig,
)
from forge.fragments import FRAGMENT_REGISTRY
from forge.generator import generate

FRAGMENT_NAME = "platform_auth_in_memory_provider"

# Files the in_memory fragment ADDS (it must not re-ship the middleware
# fragment's files — those land via platform_auth_python_middleware).
EXPECTED_FILES = (
    "src/app/security/in_memory_issuer.py",
    "src/app/security/in_memory_auth.py",
    "src/app/security/__init__.py",
    "src/app/api/v1/endpoints/dev_auth.py",
)


def _fragment_root() -> Path:
    frag = FRAGMENT_REGISTRY[FRAGMENT_NAME]
    impl = frag.implementations[BackendLanguage.PYTHON]
    return Path(impl.fragment_dir) / "files"


# --------------------------------------------------------------------------- #
# Registration + wiring
# --------------------------------------------------------------------------- #


def test_in_memory_fragment_registered() -> None:
    assert FRAGMENT_NAME in FRAGMENT_REGISTRY
    frag = FRAGMENT_REGISTRY[FRAGMENT_NAME]
    # Python-only — the in-process issuer is a FastAPI/Python construct.
    assert BackendLanguage.PYTHON in frag.implementations
    assert BackendLanguage.NODE not in frag.implementations
    assert BackendLanguage.RUST not in frag.implementations
    # Backend-scoped — files land per Python backend.
    assert frag.implementations[BackendLanguage.PYTHON].scope == "backend"


def test_in_memory_depends_on_python_middleware() -> None:
    """It imports ``AuthGuardBundle`` from the middleware fragment and injects
    into the middleware-wired factory, so the middleware must be in the plan."""
    frag = FRAGMENT_REGISTRY[FRAGMENT_NAME]
    assert "platform_auth_python_middleware" in frag.depends_on


def test_in_memory_wired_to_auth_provider_enables() -> None:
    from forge.options import OPTION_REGISTRY

    auth_provider = OPTION_REGISTRY["auth.provider"]
    enabled = auth_provider.enables.get("in_memory", ())
    assert FRAGMENT_NAME in enabled, (
        "auth.provider=in_memory's enables tuple must contain "
        f"{FRAGMENT_NAME!r} — without it the provider ships no issuer."
    )
    # in_memory must NOT pull in any gatekeeper sidecar.
    assert "platform_auth_gatekeeper" not in enabled
    assert "platform_auth_gatekeeper_keygen" not in enabled


def test_in_memory_pulls_no_infra_capabilities() -> None:
    """The whole point of in_memory is zero external infra."""
    frag = FRAGMENT_REGISTRY[FRAGMENT_NAME]
    assert frag.capabilities == (), (
        "in_memory must require no infra capabilities (no redis/keycloak/etc.)"
    )


# --------------------------------------------------------------------------- #
# Shipped-file structure
# --------------------------------------------------------------------------- #


def test_in_memory_files_shipped() -> None:
    root = _fragment_root()
    for relative in EXPECTED_FILES:
        path = root / relative
        assert path.is_file(), f"missing fragment file: {relative} (at {path})"


def test_in_memory_does_not_reship_middleware_files() -> None:
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
            f"in_memory fragment must not re-ship {owned} (owned by the "
            "middleware fragment / base template)"
        )


def test_issuer_module_uses_es256_jwks_crypto() -> None:
    """The issuer mints ES256 + exposes a JWKS, mirroring the SDK TestKeypair
    crypto approach (ECDSA P-256, ECAlgorithm.to_jwk)."""
    src = (_fragment_root() / "src/app/security/in_memory_issuer.py").read_text(encoding="utf-8")
    assert "ec.SECP256R1()" in src, "issuer must use ECDSA P-256 (ES256)"
    assert "ECAlgorithm.to_jwk" in src, "issuer must export the public JWK"
    assert "def jwks_document" in src and "def mint" in src
    assert 'algorithm="ES256"' in src
    # It builds the in_memory guard variant against its OWN JWKS (MockTransport,
    # no real network / gatekeeper).
    assert "MockTransport" in src
    assert "def build_in_memory_auth_bundle" in src
    assert "AuthGuardBundle" in src


def test_dev_route_exposes_token_endpoint() -> None:
    src = (_fragment_root() / "src/app/api/v1/endpoints/dev_auth.py").read_text(encoding="utf-8")
    assert '@router.post("/token"' in src, "must expose POST /dev/auth/token"
    # Body carries sub / scopes / tenant_id per the spec.
    for field in ("sub", "scopes", "tenant_id"):
        assert field in src, f"dev token request must accept {field!r}"


class TestProductionRefusal:
    """``install_in_memory_auth`` must fail closed under a production posture —
    the dev issuer mints arbitrary identity tokens with NO authentication, so a
    stray prod deploy must crash at boot rather than silently expose minting.
    The module imports ``app.*`` packages absent from forge's env, so the
    guard is loaded with those stubbed and exercised for real."""

    def _load_install_module(self):
        import importlib.util
        import sys
        import types

        # Stub the app.* + fastapi imports the module performs at top level.
        if "fastapi" not in sys.modules:
            fastapi_stub = types.ModuleType("fastapi")
            fastapi_stub.FastAPI = object
            sys.modules["fastapi"] = fastapi_stub
        for name in (
            "app",
            "app.core",
            "app.core.lifecycle",
            "app.core.config",
            "app.security",
            "app.security.in_memory_issuer",
        ):
            sys.modules.setdefault(name, types.ModuleType(name))
        sys.modules["app.core.config"].Settings = object
        sys.modules["app.security.in_memory_issuer"].InMemoryIssuer = object
        sys.modules["app.security.in_memory_issuer"].build_in_memory_auth_bundle = lambda *a, **k: (
            None
        )
        path = _fragment_root() / "src/app/security/in_memory_auth.py"
        spec = importlib.util.spec_from_file_location("_in_memory_auth_under_test", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return mod

    @pytest.mark.parametrize("env", ["production", "prod", "staging", "PRODUCTION", ""])
    def test_refuses_under_production_posture(self, env, monkeypatch):
        mod = self._load_install_module()
        monkeypatch.setenv("ENV", env)
        with pytest.raises(mod.InMemoryAuthInProductionError):
            mod._refuse_in_production()

    def test_refuses_when_env_unset(self, monkeypatch):
        mod = self._load_install_module()
        monkeypatch.delenv("ENV", raising=False)
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        with pytest.raises(mod.InMemoryAuthInProductionError):
            mod._refuse_in_production()

    @pytest.mark.parametrize("env", ["development", "dev", "test", "testing", "local", "ci"])
    def test_allows_dev_postures(self, env, monkeypatch):
        mod = self._load_install_module()
        monkeypatch.setenv("ENV", env)
        mod._refuse_in_production()  # must not raise

    def test_install_calls_the_guard(self) -> None:
        # The guard must actually be invoked by install_in_memory_auth, not
        # just defined.
        src = (_fragment_root() / "src/app/security/in_memory_auth.py").read_text(encoding="utf-8")
        body = src.split("def install_in_memory_auth")[1]
        assert "_refuse_in_production()" in body


def test_inject_yaml_switches_guard_and_mounts_route() -> None:
    inject = (Path(_fragment_root()).parent / "inject.yaml").read_text(encoding="utf-8")
    # Mounts the dev route on the v1 router.
    assert "dev_auth" in inject
    assert "FORGE:API_ROUTER_REGISTRATION" in inject
    # Redirects the guard builder at the factory's post-configure seam (before
    # AppLifecycle.bootstrap runs).
    assert "install_in_memory_auth" in inject
    assert "FORGE:APP_POST_CONFIGURE" in inject


# --------------------------------------------------------------------------- #
# Resolver: provider-aware keycloak coercion
# --------------------------------------------------------------------------- #


def _py_config(provider: str, *, include_keycloak: bool) -> ProjectConfig:
    return ProjectConfig(
        project_name="im",
        backends=[
            BackendConfig(
                name="api",
                project_name="im",
                language=BackendLanguage.PYTHON,
                features=["items"],
            )
        ],
        frontend=None,
        include_keycloak=include_keycloak,
        options={"auth.mode": "generate", "auth.provider": provider},
    )


def test_in_memory_resolves_without_keycloak() -> None:
    """in_memory must stay auth.mode=generate WITHOUT keycloak and pull in the
    provider fragment while EXCLUDING gatekeeper."""
    from forge.capability_resolver import resolve

    plan = resolve(_py_config("in_memory", include_keycloak=False))
    assert plan.option_values["auth.mode"] == "generate", (
        "in_memory must NOT be coerced to auth.mode=none when keycloak is off — "
        "it needs no keycloak"
    )
    assert plan.option_values["auth.provider"] == "in_memory"
    names = {rf.fragment.name for rf in plan.ordered}
    assert FRAGMENT_NAME in names
    assert "platform_auth_python_middleware" in names
    assert "platform_auth_gatekeeper" not in names
    assert "platform_auth_gatekeeper_keygen" not in names


def test_gatekeeper_still_coerced_without_keycloak() -> None:
    """The keycloak coercion must still fire for the gatekeeper provider — this
    is what keeps the golden output unchanged."""
    from forge.capability_resolver import resolve

    plan = resolve(_py_config("gatekeeper", include_keycloak=False))
    assert plan.option_values["auth.mode"] == "none"
    assert plan.option_values["auth.provider"] == "none"
    names = {rf.fragment.name for rf in plan.ordered}
    assert "platform_auth_gatekeeper" not in names
    assert "platform_auth_python_middleware" not in names


# --------------------------------------------------------------------------- #
# Render: full dry-run generation
# --------------------------------------------------------------------------- #


def test_in_memory_render_lands_issuer_and_route(tmp_path: Path) -> None:
    """Generate a Python project with auth.provider=in_memory and assert the
    in-memory issuer + dev route land, the injections apply, and NO gatekeeper
    directory is generated."""
    cfg = ProjectConfig(
        project_name="imt",
        output_dir=str(tmp_path),
        backends=[
            BackendConfig(
                name="api",
                project_name="imt",
                language=BackendLanguage.PYTHON,
                features=["items"],
            )
        ],
        frontend=None,
        include_keycloak=False,
        options={"auth.mode": "generate", "auth.provider": "in_memory"},
    )
    root = Path(generate(cfg, quiet=True, dry_run=True))
    backend = root / "services" / "api"

    # Issuer + dev route landed.
    assert (backend / "src/app/security/in_memory_issuer.py").is_file()
    assert (backend / "src/app/security/in_memory_auth.py").is_file()
    assert (backend / "src/app/api/v1/endpoints/dev_auth.py").is_file()

    # NO gatekeeper container.
    assert not (root / "deploy" / "infra" / "gatekeeper").exists(), (
        "in_memory must NOT generate the gatekeeper container"
    )

    # Dev route mounted on the v1 router.
    api_py = (backend / "src/app/api/v1/api.py").read_text(encoding="utf-8")
    assert "dev_auth_endpoint" in api_py
    assert "/dev/auth" in api_py

    # Guard-builder redirect injected into the factory BEFORE bootstrap.
    main_py = (backend / "src/app/main.py").read_text(encoding="utf-8")
    assert "install_in_memory_auth(app, settings)" in main_py
    install_idx = main_py.index("install_in_memory_auth(app, settings)")
    bootstrap_idx = main_py.index("AppLifecycle.bootstrap(app, settings")
    assert install_idx < bootstrap_idx, (
        "install_in_memory_auth must run BEFORE AppLifecycle.bootstrap so it can "
        "redirect build_auth_guard before the guard is built"
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
