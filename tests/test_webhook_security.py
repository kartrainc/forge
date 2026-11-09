"""Security regression tests for the webhooks template.

Confirmed by the audit (and a verifier):
  * cross-tenant IDOR — list/delete/test-fire used PublicUnitOfWork with no
    tenant filter and create hardcoded customer_id=_ANON;
  * unrestricted SSRF — test-fire POSTed to any stored URL.

``webhook_service.validate_outbound_url`` is pure stdlib but the module
top-imports ``app.*`` packages absent from forge's env, so we stub those in
``sys.modules`` and exercise the REAL SSRF guard. The endpoint's tenant
scoping (generated-only forge_core imports) is asserted on source.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

_BASE = Path(__file__).resolve().parent.parent
_SERVICE = (
    _BASE
    / "forge/features/platform/templates/webhooks/python/files/src/app/services/webhook_service.py"
)
_ENDPOINT = (
    _BASE
    / "forge/features/platform/templates/webhooks/python/files/src/app/api/v1/endpoints/webhooks.py"
)
_NODE_SERVICE = (
    _BASE
    / "forge/features/platform/templates/webhooks/node/files/src/services/webhook.service.ts"
)
_RUST_SERVICE = (
    _BASE
    / "forge/features/platform/templates/webhooks/rust/files/src/services/webhooks.rs"
)


def _load_service():
    """Load the real webhook_service module with its ``app.*`` imports stubbed."""
    for name in (
        "app",
        "app.data",
        "app.data.models",
        "app.data.models.webhook",
        "app.domain",
        "app.domain.webhook",
    ):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["app.data.models.webhook"].Webhook = object
    sys.modules["app.domain.webhook"].WebhookDeliveryResult = object
    spec = importlib.util.spec_from_file_location("_webhook_service_under_test", _SERVICE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def svc(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    return _load_service()


class TestSsrfGuard:
    @pytest.mark.parametrize(
        "url",
        [
            "http://169.254.169.254/latest/meta-data/",  # cloud metadata (link-local)
            "https://127.0.0.1/internal",  # loopback
            "https://localhost/internal",  # loopback by name
            "https://10.0.0.5/admin",  # RFC1918
            "https://192.168.1.1/",  # RFC1918
            "https://172.16.0.1/",  # RFC1918
            "https://[::1]/",  # IPv6 loopback
            "https://0.0.0.0/",  # unspecified
        ],
    )
    def test_internal_targets_rejected(self, svc, url):
        with pytest.raises(svc.WebhookUrlError):
            svc.validate_outbound_url(url)

    def test_ipv4_mapped_ipv6_loopback_rejected(self, svc):
        with pytest.raises(svc.WebhookUrlError):
            svc.validate_outbound_url("https://[::ffff:127.0.0.1]/")

    def test_non_http_scheme_rejected(self, svc):
        for url in ("file:///etc/passwd", "gopher://x/", "ftp://host/"):
            with pytest.raises(svc.WebhookUrlError):
                svc.validate_outbound_url(url)

    def test_http_rejected_in_production(self, svc):
        with pytest.raises(svc.WebhookUrlError):
            svc.validate_outbound_url("http://example.com/hook")

    @staticmethod
    def _stub_public_dns(monkeypatch, svc):
        # Resolve any host to a public IP so the test never depends on real DNS.
        monkeypatch.setattr(
            svc.socket,
            "getaddrinfo",
            lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))],
        )

    def test_http_allowed_in_dev(self, monkeypatch):
        monkeypatch.setenv("ENV", "development")
        svc = _load_service()
        self._stub_public_dns(monkeypatch, svc)
        # A public host over http is fine in dev (no raise).
        svc.validate_outbound_url("http://example.com/hook")

    def test_public_https_target_allowed(self, svc, monkeypatch):
        self._stub_public_dns(monkeypatch, svc)
        # A public host must pass (resolves to a routable address).
        svc.validate_outbound_url("https://example.com/hook")

    def test_dns_rebinding_to_internal_rejected(self, svc, monkeypatch):
        # Even if a public-looking host resolves to an internal IP, block it.
        monkeypatch.setattr(
            svc.socket,
            "getaddrinfo",
            lambda *a, **k: [(2, 1, 6, "", ("10.1.2.3", 443))],
        )
        with pytest.raises(svc.WebhookUrlError):
            svc.validate_outbound_url("https://totally-public.example/hook")


class TestEndpointTenantScoping:
    def _src(self) -> str:
        return _ENDPOINT.read_text(encoding="utf-8")

    def test_uses_tenant_bound_uow_not_public(self):
        src = self._src()
        assert "PublicUnitOfWork" not in src
        assert "AuthUnitOfWork" in src

    def test_no_anon_customer_on_create(self):
        src = self._src()
        assert "_ANON" not in src
        assert "customer_id=_tenant_id(user)" in src

    def test_queries_filter_by_verified_tenant(self):
        src = self._src()
        # Every read/delete/test-fire must filter by the caller's tenant.
        assert src.count("WebhookModel.customer_id == cid") >= 3

    def test_create_validates_url(self):
        src = self._src()
        assert "validate_outbound_url(str(data.url))" in src

    def test_router_still_auth_gated(self):
        src = self._src()
        assert "from forge_core.security.auth import get_current_user" in src
        assert "dependencies=[Depends(get_current_user)]" in src


class TestDeliveryHardening:
    def test_deliver_validates_before_post(self, svc):
        src = _SERVICE.read_text(encoding="utf-8")
        body = src.split("async def deliver")[1]
        assert "validate_outbound_url(webhook.url)" in body
        assert "follow_redirects=False" in body

    def test_deliver_uses_connect_time_guard_transport(self, svc):
        # The authoritative anti-rebinding control: deliver must connect via the
        # guard transport, and post the ORIGINAL webhook.url (so Host/SNI/cert
        # verification stay correct) rather than a rewritten IP URL.
        src = _SERVICE.read_text(encoding="utf-8")
        body = src.split("async def deliver")[1]
        assert "transport=_guarded_transport()" in body
        assert "client.post(webhook.url" in body

    def test_guard_backend_validates_at_connect(self, svc):
        # The backend wrapper must validate the host at connect_tcp (closing the
        # TOCTOU a pre-flight check alone leaves open).
        src = _SERVICE.read_text(encoding="utf-8")
        gt = src.split("def _guarded_transport")[1]
        assert "async def connect_tcp" in gt
        assert "_resolve_and_validate(host, port)" in gt
        assert "connect_tcp(\n                validated_ip" in gt

    def test_resolve_and_validate_blocks_internal_literals(self, svc):
        for bad in ("127.0.0.1", "169.254.169.254", "10.0.0.1", "192.168.0.1", "::1"):
            with pytest.raises(svc.WebhookUrlError):
                svc._resolve_and_validate(bad, 443)

    def test_resolve_and_validate_blocks_rebinding_resolution(self, svc, monkeypatch):
        monkeypatch.setattr(
            svc.socket, "getaddrinfo",
            lambda *a, **k: [(2, 1, 6, "", ("10.1.2.3", 443))],
        )
        with pytest.raises(svc.WebhookUrlError):
            svc._resolve_and_validate("totally-public.example", 443)


class TestNodeDeliveryHardening:
    """The Node delivery path must reach SSRF/open-redirect parity with Python:
    a host guard that rejects internal targets AND redirect suppression so a
    3xx to an internal host cannot bypass the guard."""

    def _src(self) -> str:
        return _NODE_SERVICE.read_text(encoding="utf-8")

    def test_has_outbound_url_guard(self):
        src = self._src()
        assert "validateOutboundUrl" in src

    def test_guard_rejects_internal_and_non_http(self):
        src = self._src()
        # Loopback / link-local (cloud metadata) / RFC1918 ranges must be named.
        assert "127.0.0" in src
        assert "169.254" in src
        assert ("10." in src and "192.168" in src and "172." in src)
        # Non-http(s) schemes must be rejected by the guard.
        assert "https:" in src and "http:" in src

    def test_deliver_validates_before_fetch(self):
        src = self._src()
        body = src.split("export async function deliver")[1]
        assert "validateOutboundUrl(webhook.url)" in body

    def test_deliver_suppresses_redirects(self):
        src = self._src()
        body = src.split("export async function deliver")[1]
        # fetch must not auto-follow 3xx to an internal host.
        assert ('redirect: "manual"' in body) or ('redirect: "error"' in body)

    def test_imports_dns_promises(self):
        # DNS-resolution SSRF: the delivery path must resolve names, not just
        # check literals. Node 18+ has dns/promises built in (no new dep).
        src = self._src()
        assert ("node:dns/promises" in src) or ("node:dns" in src and "promises" in src)

    def test_deliver_resolves_host_before_fetch(self):
        # Before fetching, deliver must resolve the URL host via dns lookup
        # (all addresses) and validate every resolved IP against the existing
        # block predicate, closing the static-DNS-to-private-IP hole.
        src = self._src()
        body = src.split("export async function deliver")[1]
        # Resolve all addresses for the host.
        assert "lookup(" in body
        assert "all: true" in body
        # Validate each RESOLVED address with the existing block predicate.
        assert ("isBlockedIp" in body) or ("isBlockedHost" in body)


class TestRustDeliveryHardening:
    """The Rust delivery path must reach SSRF/open-redirect parity with Python:
    a host guard that rejects internal targets AND a redirect policy of none()
    (reqwest follows up to 10 redirects by default)."""

    def _src(self) -> str:
        return _RUST_SERVICE.read_text(encoding="utf-8")

    def test_has_outbound_url_guard(self):
        src = self._src()
        assert "fn validate_outbound_url" in src

    def test_guard_rejects_internal_and_non_http(self):
        src = self._src()
        assert "127.0.0" in src
        assert "169.254" in src
        assert ("10." in src and "192.168" in src and "172." in src)
        assert '"https"' in src and '"http"' in src

    def test_deliver_validates_before_post(self):
        src = self._src()
        body = src.split("pub async fn deliver")[1]
        assert "validate_outbound_url(&webhook.url)" in body

    def test_deliver_disables_redirects(self):
        src = self._src()
        body = src.split("pub async fn deliver")[1]
        assert "redirect::Policy::none()" in body

    def test_no_new_crates_added(self):
        # Host/scheme parsing must be manual (no new crate import) so the
        # rust deps in fragments.py stay untouched.
        src = self._src()
        assert "use url::" not in src
        assert "use addr::" not in src

    def test_resolves_host_via_to_socket_addrs(self):
        # DNS-resolution SSRF: deliver must resolve the host (std only) and
        # reject if any resolved IpAddr is internal — not just literal hosts.
        src = self._src()
        assert "to_socket_addrs" in src
        # The std resolver trait must be in scope.
        assert "ToSocketAddrs" in src

    def test_deliver_validates_resolved_ipaddr(self):
        # Each resolved address must be checked as an IpAddr against the
        # existing block logic (is_blocked_ipv4 / is_blocked_host).
        src = self._src()
        assert "IpAddr" in src
        assert ("is_blocked_ipv4" in src) or ("is_blocked_ip" in src)
