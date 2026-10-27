"""The gatekeeper resolves every frontend's host to the project's realm.

A project with several frontends serves each on its own host. Without a
``tenant-route:<host>`` record, ``get_fallback_config`` signs every host in to
the admin realm; with ``REALM_PER_HOST`` it takes the host's first label as the
realm, except for the admin realm's label and the labels listed in
``GATEKEEPER_FRONTEND_HOST_LABELS``. ``tenant_config.py`` is loaded from the
template with a stub for its one import, the settings; the rendered module is
exercised against the real settings by ``tests/runtime/gatekeeper_contract.py``.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

_GATEKEEPER = (
    Path(__file__).resolve().parent.parent
    / "forge/features/auth/templates/platform_auth_gatekeeper/all/files"
    / "deploy/infra/gatekeeper/src/app/gatekeeper"
)
_DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"


def _fallback(monkeypatch: pytest.MonkeyPatch, labels: str, *, realm_per_host: bool = True):
    settings = SimpleNamespace(
        gatekeeper_frontend_host_labels=labels,
        realm_per_host=realm_per_host,
        keycloak_admin_realm="app",
        default_tenant_id=_DEFAULT_TENANT,
        keycloak_base_url="http://keycloak:8080/realms",
        gatekeeper_client_id="gatekeeper",
        gatekeeper_client_secret="secret",
        default_rate_limit=100,
        default_idle_timeout_seconds=0,
        default_absolute_timeout_seconds=0,
    )
    config = types.ModuleType("app.gatekeeper.config")
    config.get_settings = lambda: settings  # type: ignore[attr-defined]
    for name, module in (
        ("app", types.ModuleType("app")),
        ("app.gatekeeper", types.ModuleType("app.gatekeeper")),
        ("app.gatekeeper.config", config),
    ):
        monkeypatch.setitem(sys.modules, name, module)
    spec = importlib.util.spec_from_file_location(
        "gk_tenant_config_under_test", _GATEKEEPER / "tenant_config.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)  # dataclasses look it up
    spec.loader.exec_module(module)
    return module.get_fallback_config


@pytest.mark.parametrize(
    ("labels", "host", "realm", "tenant"),
    [
        ("", "app", "app", _DEFAULT_TENANT),
        ("", "acme", "acme", "acme"),
        ("admin", "admin", "app", _DEFAULT_TENANT),
        (" admin , portal ", "portal", "app", _DEFAULT_TENANT),
        ("admin", "acme", "acme", "acme"),
        (",", "", "", ""),
    ],
)
def test_host_labels_resolve_to_the_admin_realm(
    monkeypatch: pytest.MonkeyPatch, labels: str, host: str, realm: str, tenant: str
) -> None:
    resolved = _fallback(monkeypatch, labels)(host)
    assert (resolved.realm_name, resolved.tenant_id) == (realm, tenant)
    assert resolved.issuer_url == f"http://keycloak:8080/realms/{realm}"


@pytest.mark.parametrize("host", ["app", "admin", "acme"])
def test_without_realm_per_host_every_host_uses_the_admin_realm(
    monkeypatch: pytest.MonkeyPatch, host: str
) -> None:
    resolved = _fallback(monkeypatch, "admin", realm_per_host=False)(host)
    assert (resolved.realm_name, resolved.tenant_id) == ("app", _DEFAULT_TENANT)


def test_the_setting_is_declared_with_an_empty_default() -> None:
    source = (_GATEKEEPER / "config.py").read_text(encoding="utf-8")
    assert 'gatekeeper_frontend_host_labels: str = ""' in source
