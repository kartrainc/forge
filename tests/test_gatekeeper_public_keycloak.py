"""The browser reaches Keycloak at ``KEYCLOAK_PUBLIC_BASE_URL``.

The gatekeeper calls Keycloak in the cluster at ``KEYCLOAK_BASE_URL``; the
sign-in and sign-out redirects, and the issuer Keycloak names under
``KC_HOSTNAME``, use the public URL when it is set. ``helpers.py`` is loaded
from the template with stubs for its two imports; the rendered gatekeeper is
exercised end to end by ``tests/runtime/gatekeeper_contract.py``.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

_GATEKEEPER = (
    Path(__file__).resolve().parent.parent
    / "forge/features/auth/templates/platform_auth_gatekeeper/all/files"
    / "deploy/infra/gatekeeper/src/app/gatekeeper"
)
_INTERNAL = "http://keycloak:8080/realms"
_PUBLIC = "https://auth.example.com/realms"


def _helpers(monkeypatch: pytest.MonkeyPatch, public: str = ""):
    settings = SimpleNamespace(
        keycloak_base_url=_INTERNAL,
        keycloak_public_base_url=public,
        gatekeeper_client_id="gatekeeper",
    )
    config = types.ModuleType("app.gatekeeper.config")
    config.get_settings = lambda: settings  # type: ignore[attr-defined]
    fastapi = types.ModuleType("fastapi")
    fastapi.Response = object  # type: ignore[attr-defined]
    for name, module in (
        ("fastapi", fastapi),
        ("app", types.ModuleType("app")),
        ("app.gatekeeper", types.ModuleType("app.gatekeeper")),
        ("app.gatekeeper.config", config),
    ):
        monkeypatch.setitem(sys.modules, name, module)
    spec = importlib.util.spec_from_file_location(
        "gk_helpers_under_test", _GATEKEEPER / "helpers.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def _endpoint(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


@pytest.mark.parametrize(
    "issuer_url", [None, f"{_INTERNAL}/tms", "https://idp.example.org/realms/other"]
)
def test_without_a_public_url_redirects_are_unchanged(
    monkeypatch: pytest.MonkeyPatch, issuer_url: str | None
) -> None:
    helpers = _helpers(monkeypatch)
    realm_url = issuer_url or f"{_INTERNAL}/app"
    login = helpers.build_login_url("app", "https://app/callback", "s", issuer_url=issuer_url)
    logout = helpers.build_logout_url("app", issuer_url=issuer_url)
    assert _endpoint(login) == f"{realm_url}/protocol/openid-connect/auth"
    assert _endpoint(logout) == f"{realm_url}/protocol/openid-connect/logout"
    assert helpers.public_realm_url("app", issuer_url) == realm_url


@pytest.mark.parametrize(
    ("issuer_url", "browser"),
    [
        (None, f"{_PUBLIC}/app"),
        (f"{_INTERNAL}/tms", f"{_PUBLIC}/tms"),
        (f"{_INTERNAL}/", f"{_PUBLIC}/"),
        ("https://idp.example.org/realms/other", "https://idp.example.org/realms/other"),
        # Only a whole path segment of the in-cluster base moves.
        ("http://keycloak:8080/realmsx/app", "http://keycloak:8080/realmsx/app"),
    ],
)
@pytest.mark.parametrize("public", [_PUBLIC, f"{_PUBLIC}/"])
def test_the_browser_is_sent_to_the_public_url(
    monkeypatch: pytest.MonkeyPatch, issuer_url: str | None, browser: str, public: str
) -> None:
    helpers = _helpers(monkeypatch, public)
    login = helpers.build_login_url(
        "app", "https://app/callback", "state", issuer_url=issuer_url, nonce="n"
    )
    logout = helpers.build_logout_url(
        "app", issuer_url=issuer_url, post_logout_redirect_uri="https://app/"
    )
    assert _endpoint(login) == f"{browser}/protocol/openid-connect/auth"
    assert _endpoint(logout) == f"{browser}/protocol/openid-connect/logout"
    query = parse_qs(urlsplit(login).query)
    assert query["redirect_uri"] == ["https://app/callback"]
    assert query["client_id"] == ["gatekeeper"]
    assert helpers.public_realm_url("app", issuer_url) == browser


def test_only_public_realm_url_reads_the_setting() -> None:
    """Back-channel calls cannot pick the public URL up by accident."""
    readers = {
        path.relative_to(_GATEKEEPER.parent).as_posix()
        for path in _GATEKEEPER.parent.rglob("*.py")
        if "keycloak_public_base_url" in path.read_text(encoding="utf-8")
    }
    assert readers == {"gatekeeper/config.py", "gatekeeper/helpers.py"}


def test_the_setting_is_declared_with_an_empty_default() -> None:
    source = (_GATEKEEPER / "config.py").read_text(encoding="utf-8")
    assert 'keycloak_public_base_url: str = ""' in source
    assert "| KEYCLOAK_PUBLIC_BASE_URL " in source
