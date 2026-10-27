"""Regression guard for the PR #170 node/rust image-build break.

``generator.py`` gates each backend's ``COPY --from=packages`` (and the
platform-auth dependency wiring) on whether the backend's OWN-language
auth middleware fragment is in the resolved plan. The check originally
hardcoded the Python fragment, so a Node/Rust backend with auth but no
Python sibling rendered its Dockerfile WITHOUT the sdks COPY while the
project still shipped ``sdks/`` and declared the ``file:``/``path``
dependency — every ``docker build`` then failed (TS2307 for node, missing
Cargo manifest for rust). The 2026-06-09/10 nightly went red on exactly
the auth-without-python-backend smoke scenarios.

These tests assert the rendered Dockerfile + project tree stay consistent
for every single-language auth project, via dry-run generation (no
toolchains required).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forge.config import (
    BackendConfig,
    BackendLanguage,
    FrontendConfig,
    FrontendFramework,
    ProjectConfig,
)
from forge.generator import _PLATFORM_AUTH_MIDDLEWARE, generate


def _auth_project(name: str, language: BackendLanguage, tmp_path: Path) -> ProjectConfig:
    return ProjectConfig(
        project_name=name,
        output_dir=str(tmp_path),
        backends=[
            BackendConfig(
                name="api",
                project_name=name,
                language=language,
            )
        ],
        frontends=[
            FrontendConfig(
                project_name=name,
                framework=FrontendFramework.VUE,
                include_auth=True,
            )
        ],
        include_keycloak=True,
    )


@pytest.mark.parametrize(
    "language",
    [BackendLanguage.PYTHON, BackendLanguage.NODE, BackendLanguage.RUST, BackendLanguage.GO],
)
def test_auth_backend_dockerfile_copies_sdks(language, tmp_path):
    """When a backend ships platform-auth, its Dockerfile must COPY the
    sdks tree the project actually emits — regardless of the backend's
    language (the bug only manifested for node/rust)."""
    cfg = _auth_project(f"auth_{language.value}", language, tmp_path)
    root = generate(cfg, quiet=True, dry_run=True)

    packages_dir = root / "packages"
    assert packages_dir.is_dir(), "auth project must ship the packages/ tree"

    dockerfile = (root / "services" / "api" / "Dockerfile").read_text(encoding="utf-8")
    assert "--from=packages" in dockerfile, (
        f"{language.value} auth backend ships packages/ but its Dockerfile has no "
        f"COPY --from=packages — the image build will fail on the unresolved "
        f"platform-auth dependency"
    )


def test_middleware_map_covers_every_builtin_language():
    """Every built-in backend language must map to a real middleware
    fragment, or its auth backends silently lose the sdks COPY."""
    from forge.fragments import FRAGMENT_REGISTRY

    for language in BackendLanguage:
        frag = _PLATFORM_AUTH_MIDDLEWARE.get(language)
        assert frag is not None, f"no auth-middleware fragment mapped for {language}"
        assert frag in FRAGMENT_REGISTRY, f"{frag} not in the fragment registry"


def test_noauth_backend_dockerfile_omits_sdks_copy(tmp_path):
    """The inverse: a no-auth project ships no packages/ tree, so the COPY must
    be absent (an unconditional COPY would fail the build)."""
    cfg = ProjectConfig(
        project_name="noauth_node",
        output_dir=str(tmp_path),
        backends=[
            BackendConfig(name="api", project_name="noauth_node", language=BackendLanguage.NODE)
        ],
        frontends=[
            FrontendConfig(
                project_name="noauth_node",
                framework=FrontendFramework.VUE,
                include_auth=False,
            )
        ],
        include_keycloak=False,
    )
    root = generate(cfg, quiet=True, dry_run=True)
    dockerfile = (root / "services" / "api" / "Dockerfile").read_text(encoding="utf-8")
    assert "--from=packages" not in dockerfile
