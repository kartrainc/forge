"""Invariants for the ``forge.features.auth`` Python SDK fragment (Phase 1).

Verifies that the platform-auth Python SDK port wires through forge's
option/fragment registries correctly and that the fragment's template
tree contains every public-surface module from the upstream SDK.

Cross-reference: implementation plan at
``~/.claude/plans/review-the-c-users-chifo-work-platform-a-pure-torvalds.md``
(Phase 1 deliverables).
"""

from __future__ import annotations

from pathlib import Path

from forge.config import BackendLanguage
from forge.fragments import FRAGMENT_REGISTRY
from forge.options import OPTION_REGISTRY

PUBLIC_SDK_MODULES = (
    "__init__.py",
    "auth_guard.py",
    "exceptions.py",
    "identity.py",
    "jwks.py",
    "may_act.py",
    "revocation.py",
    "s2s_client.py",
    "scopes.py",
    "testing.py",
    "trust.py",
)


def test_auth_mode_option_registered() -> None:
    assert "auth.mode" in OPTION_REGISTRY
    opt = OPTION_REGISTRY["auth.mode"]
    assert opt.default == "generate"
    assert opt.options == ("generate", "none")
    # Provider-discriminator split: auth.mode=generate now enables the 11
    # issuer-agnostic auth fragments (3 SDKs + 3 frontend session-timeout +
    # 4 backend middleware + 1 tenant-context; the Go middleware pulls in
    # platform_auth_sdk_go through depends_on). The 2 gatekeeper fragments
    # moved to auth.provider=gatekeeper (the default), so the resolved set is
    # unchanged for a default auth.mode=generate project (golden-verified).
    enabled = opt.enables["generate"]
    assert "platform_auth_sdk_python" in enabled
    assert "platform_auth_sdk_node" in enabled
    assert "platform_auth_sdk_rust" in enabled
    assert "platform_auth_session_timeout_vue" in enabled
    assert "platform_auth_session_timeout_svelte" in enabled
    assert "platform_auth_session_timeout_flutter" in enabled
    assert "platform_auth_python_middleware" in enabled
    assert "platform_auth_node_middleware" in enabled
    assert "platform_auth_rust_middleware" in enabled
    assert "platform_auth_go_middleware" in enabled
    assert "platform_auth_tenant_context" in enabled
    # Gatekeeper is the issuer — now selected by auth.provider, NOT auth.mode.
    assert "platform_auth_gatekeeper" not in enabled
    assert "platform_auth_gatekeeper_keygen" not in enabled
    assert len(enabled) == 11, (
        f"auth.mode=generate should enable the 11 issuer-agnostic auth "
        f"fragments after the provider split; got {len(enabled)}: {enabled}"
    )
    provider = OPTION_REGISTRY["auth.provider"]
    assert provider.default == "gatekeeper"
    gk = provider.enables["gatekeeper"]
    assert "platform_auth_gatekeeper" in gk
    assert "platform_auth_gatekeeper_keygen" in gk


def test_platform_auth_sdk_python_fragment_registered() -> None:
    assert "platform_auth_sdk_python" in FRAGMENT_REGISTRY
    frag = FRAGMENT_REGISTRY["platform_auth_sdk_python"]
    assert BackendLanguage.PYTHON in frag.implementations
    assert BackendLanguage.NODE not in frag.implementations
    assert BackendLanguage.RUST not in frag.implementations
    impl = frag.implementations[BackendLanguage.PYTHON]
    assert impl.scope == "project"
    assert frag.parity_tier == 3  # python-only until Phases 4 + 6


def test_platform_auth_sdk_files_present() -> None:
    """The fragment's ``files/`` tree must ship every public SDK module."""
    frag = FRAGMENT_REGISTRY["platform_auth_sdk_python"]
    impl = frag.implementations[BackendLanguage.PYTHON]
    src_dir = (
        Path(impl.fragment_dir) / "files" / "packages" / "platform-auth" / "src" / "platform_auth"
    )
    assert src_dir.is_dir(), f"SDK src/ tree missing: {src_dir}"
    shipped = {p.name for p in src_dir.glob("*.py")}
    missing = set(PUBLIC_SDK_MODULES) - shipped
    assert not missing, f"SDK modules not shipped: {sorted(missing)}"


def test_platform_auth_sdk_pyproject_targets_supported_pythons() -> None:
    """pyproject must require Python 3.11+ (forge's CI matrix floor)."""
    frag = FRAGMENT_REGISTRY["platform_auth_sdk_python"]
    impl = frag.implementations[BackendLanguage.PYTHON]
    pyproject = Path(impl.fragment_dir) / "files" / "packages" / "platform-auth" / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    assert 'requires-python = ">=3.11"' in text, (
        "pyproject.toml must lower the >=3.13 floor from the upstream "
        "platform SDK to >=3.11 to match forge's CI matrix."
    )


def test_platform_auth_sdk_testing_helper_uses_aligned_claim_kwargs() -> None:
    """Python ``build_test_token`` must accept ``roles_claim`` /
    ``scope_claim`` kwargs to match Node ``BuildTestTokenOptions.rolesClaim``
    / ``scopeClaim`` and Rust ``BuildTestTokenOptions.roles_claim`` /
    ``scope_claim``.

    Until 2026-05 Python alone hardcoded ``"roles"`` and ``"scope"`` as the
    JWT-payload keys. This blocked custom-claim-name parity scenarios from
    being added to the cross-SDK gate, since Python couldn't mint a token
    matching what Node + Rust verifiers configured with custom claim names
    would expect to verify.

    Pinning the parameter names + their defaults so a future refactor
    can't strip the configurability or rename the kwargs out of alignment.
    """
    frag = FRAGMENT_REGISTRY["platform_auth_sdk_python"]
    impl = frag.implementations[BackendLanguage.PYTHON]
    testing_text = (
        Path(impl.fragment_dir)
        / "files"
        / "packages"
        / "platform-auth"
        / "src"
        / "platform_auth"
        / "testing.py"
    ).read_text(encoding="utf-8")
    must_have = (
        # Parameter declarations with cross-language-aligned defaults.
        'roles_claim: str = "roles"',
        'scope_claim: str = "scope"',
        # Parameters must actually wire through to the claim dict —
        # not merely accepted-and-ignored.
        "scope_claim: scope_value",
        "roles_claim: list(roles)",
    )
    missing = [name for name in must_have if name not in testing_text]
    assert not missing, f"build_test_token missing aligned claim-name kwargs: {missing}"


def test_platform_auth_sdk_audit_callback_shape_matches_cross_sdk_contract() -> None:
    """Python ``_emit_audit`` is the canonical reference for the
    cross-language audit-record shape — Node + Rust mirror it.

    Pinning the field set here so a future refactor can't drop a
    field that downstream consumers (Splunk, Loki, S3+Athena) treat
    as part of the public schema. The same fields must be present in
    the Rust ``AuthAuditRecord`` struct (pinned by
    ``test_rust_sdk_audit_callback_module_present``) and the Node
    ``AuthAuditRecord`` type (pinned by
    ``test_node_sdk_audit_callback_shape_matches_cross_sdk_contract``).
    """
    frag = FRAGMENT_REGISTRY["platform_auth_sdk_python"]
    impl = frag.implementations[BackendLanguage.PYTHON]
    auth_guard = (
        Path(impl.fragment_dir)
        / "files"
        / "packages"
        / "platform-auth"
        / "src"
        / "platform_auth"
        / "auth_guard.py"
    ).read_text(encoding="utf-8")
    must_have = (
        "async def _emit_audit",
        # Cross-language record fields.
        '"decision": decision',
        '"audience": self._audiences[0]',
        '"audiences": list(self._audiences)',
        '"ts_unix": time.time()',
        '"tenant_id"',
        '"tenant_slug"',
        '"subject"',
        '"actor"',
        '"scopes"',
        '"jti"',
        '"iss"',
        '"reason"',
        # AuthGuard.__init__ must accept the optional callback kwarg.
        "audit: AuditCallback",
        "self._audit = audit",
    )
    missing = [name for name in must_have if name not in auth_guard]
    assert not missing, f"auth_guard.py missing audit-record wiring: {missing}"


def test_platform_auth_sdk_emit_audit_fires_only_on_allow_path() -> None:
    """Cross-SDK contract: Python emits on allow only; deny is reserved.

    Pinned in lockstep with Node + Rust so a future change to fire
    on deny lands across all three SDKs at once.
    """
    frag = FRAGMENT_REGISTRY["platform_auth_sdk_python"]
    impl = frag.implementations[BackendLanguage.PYTHON]
    auth_guard = (
        Path(impl.fragment_dir)
        / "files"
        / "packages"
        / "platform-auth"
        / "src"
        / "platform_auth"
        / "auth_guard.py"
    ).read_text(encoding="utf-8")
    # Exactly one allow-path call site (the success path before
    # `return identity`).
    allow_count = auth_guard.count('decision="allow"')
    assert allow_count == 1, f"expected exactly 1 allow-path _emit_audit call, found {allow_count}"
    deny_count = auth_guard.count('decision="deny"')
    assert deny_count == 0, (
        "deny-path _emit_audit is reserved for forward-compat parity; "
        "Node + Rust currently don't emit on deny either"
    )


def test_platform_auth_sdk_tests_shipped() -> None:
    """The fragment must also ship the upstream unit-test suite verbatim."""
    frag = FRAGMENT_REGISTRY["platform_auth_sdk_python"]
    impl = frag.implementations[BackendLanguage.PYTHON]
    test_dir = Path(impl.fragment_dir) / "files" / "packages" / "platform-auth" / "tests" / "unit"
    assert test_dir.is_dir()
    shipped_tests = {p.name for p in test_dir.glob("test_*.py")}
    expected = {
        "test_auth_guard.py",
        "test_exceptions.py",
        "test_identity.py",
        "test_jwks.py",
        "test_may_act.py",
        "test_require_scope.py",
        "test_revocation.py",
        "test_s2s_client.py",
        "test_s2s_client_tenant.py",
        "test_scopes.py",
        "test_trust.py",
    }
    missing = expected - shipped_tests
    assert not missing, f"unit tests missing from SDK fragment: {sorted(missing)}"
