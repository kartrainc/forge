"""Dev-identity fallback for no-auth Rust backends.

Mirror of the Node fallback (``test_features_auth_node_dev_identity``): a Rust
backend generated WITHOUT auth (``auth.mode=none``) must insert a default
``IdentityContext`` request extension, else handlers that extract
``Extension<IdentityContext>`` fail at runtime with ``Missing request
extension: api::identity::IdentityContext`` and every CRUD endpoint 500s.

Security invariant: the dev identity layer is added ONLY when auth is disabled
— never alongside the real platform-auth middleware.
"""

from __future__ import annotations

from pathlib import Path

from forge.config import BackendConfig, BackendLanguage, ProjectConfig
from forge.generator import generate


def _rust_cfg(tmp_path: Path, *, include_keycloak: bool) -> ProjectConfig:
    return ProjectConfig(
        project_name="ri",
        output_dir=str(tmp_path),
        backends=[
            BackendConfig(
                name="api",
                project_name="ri",
                language=BackendLanguage.RUST,
                features=["items"],
            )
        ],
        frontend=None,
        include_keycloak=include_keycloak,
    )


def test_noauth_rust_binds_default_identity(tmp_path: Path) -> None:
    root = Path(generate(_rust_cfg(tmp_path, include_keycloak=False), quiet=True, dry_run=True))
    api = root / "services" / "api"
    identity = (api / "src" / "identity.rs").read_text(encoding="utf-8")
    app = (api / "src" / "app.rs").read_text(encoding="utf-8")

    assert "fn dev(" in identity, "identity.rs must ship a dev() constructor"
    assert "IdentityContext::dev()" in app, (
        "no-auth rust app.rs must insert the dev IdentityContext extension "
        "(else Extension<IdentityContext> handlers 500 with 'Missing request extension')"
    )
    assert "Extension" in app, "no-auth rust app.rs must add an axum Extension layer"


def test_auth_rust_does_not_bind_dev_identity(tmp_path: Path) -> None:
    root = Path(generate(_rust_cfg(tmp_path, include_keycloak=True), quiet=True, dry_run=True))
    app = (root / "services" / "api" / "src" / "app.rs").read_text(encoding="utf-8")
    assert "IdentityContext::dev()" not in app, (
        "real-auth rust app.rs must NOT insert a dev identity — the platform-auth "
        "middleware owns identity and must reject unauthenticated requests"
    )
