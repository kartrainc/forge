"""Dev-identity fallback for no-auth Node backends.

A Node backend generated WITHOUT auth (``auth.mode=none``) must still bind a
default request identity, mirroring the Python dev passthrough
(``forge_core.security.auth`` synthesises a fixed dev user when auth is
disabled). Without it the tenant-scoped item repository dereferences an
undefined ``req.identity`` (``Cannot read properties of undefined (reading
'tenantId')``) and every CRUD endpoint 500s.

Security invariant: the dev identity is bound ONLY when auth is disabled —
never alongside the real platform-auth plugin (which 401s unauthenticated
requests).
"""

from __future__ import annotations

from pathlib import Path

from forge.config import BackendConfig, BackendLanguage, ProjectConfig
from forge.generator import generate


def _node_cfg(tmp_path: Path, *, include_keycloak: bool) -> ProjectConfig:
    return ProjectConfig(
        project_name="ni",
        output_dir=str(tmp_path),
        backends=[
            BackendConfig(
                name="api",
                project_name="ni",
                language=BackendLanguage.NODE,
                features=["items"],
            )
        ],
        frontend=None,
        include_keycloak=include_keycloak,
    )


def test_noauth_node_binds_default_identity(tmp_path: Path) -> None:
    root = Path(generate(_node_cfg(tmp_path, include_keycloak=False), quiet=True, dry_run=True))
    api = root / "services" / "api"
    devmod = api / "src" / "middleware" / "dev-identity.ts"
    app = (api / "src" / "app.ts").read_text(encoding="utf-8")

    assert devmod.is_file(), "dev-identity fallback module not shipped in base node template"
    assert "registerDevIdentity" in app, (
        "no-auth node app.ts must register the dev-identity fallback hook so "
        "req.identity is bound (else tenant-scoped CRUD 500s)"
    )
    # The default identity must carry a non-null tenant + subject the repo scopes on.
    src = devmod.read_text(encoding="utf-8")
    assert "00000000-0000-0000-0000-000000000001" in src, (
        "dev identity must carry the canonical default tenant/subject UUID"
    )


def test_auth_node_does_not_bind_dev_identity(tmp_path: Path) -> None:
    root = Path(generate(_node_cfg(tmp_path, include_keycloak=True), quiet=True, dry_run=True))
    app = (root / "services" / "api" / "src" / "app.ts").read_text(encoding="utf-8")
    assert "registerDevIdentity" not in app, (
        "real-auth node app.ts must NOT bind a dev identity — the platform-auth "
        "plugin owns identity and must 401 unauthenticated requests"
    )
    assert "bootstrapAuth" in app, "real-auth node app.ts must wire bootstrapAuth"
