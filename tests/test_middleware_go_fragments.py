"""Go ``pii_redaction`` and ``response_cache`` fragments."""

from __future__ import annotations

from pathlib import Path

from forge.config import BackendConfig, BackendLanguage, ProjectConfig
from forge.fragments import FRAGMENT_REGISTRY
from forge.generator import generate

RFC_006 = (
    Path(__file__).resolve().parents[1] / "docs/rfcs/RFC-006-cross-backend-fragment-contract.md"
)


def _go_service(options: dict[str, object]) -> Path:
    config = ProjectConfig(
        project_name="P",
        backends=[
            BackendConfig(
                name="api", project_name="P", language=BackendLanguage.GO, server_port=8100
            )
        ],
        frontends=[],
        include_keycloak=False,
        options={"auth.mode": "none", **options},
    )
    root = generate(config, quiet=True, dry_run=True)
    return root / "services" / "api"


def _read(service: Path, relative: str) -> str:
    return (service / relative).read_text(encoding="utf-8")


def test_go_response_cache_prefix_defaults_per_service() -> None:
    go = FRAGMENT_REGISTRY["response_cache"].implementations[BackendLanguage.GO]
    assert ("RESPONSE_CACHE_PREFIX", "") in go.env_vars

    service = _go_service({"middleware.response_cache": True})
    assert "RESPONSE_CACHE_PREFIX=\n" in _read(service, ".env.example")
    start = _read(service, "internal/app/response_cache.go")
    assert 'os.Getenv("RESPONSE_CACHE_PREFIX"), "api:cache")' in start
    assert "forge:cache" not in start


def test_go_pii_redaction_covers_every_binary() -> None:
    service = _go_service({"middleware.pii_redaction": True, "async.task_queue": True})
    assert "WrapLogHandler(pii.NewHandler)" in _read(service, "internal/app/pii_redaction.go")
    assert "func WrapLogHandler(" in _read(service, "internal/app/server.go")
    for binary in ("server", "worker", "migrate"):
        assert "slog.SetDefault(app.Logger(" in _read(service, f"cmd/{binary}/main.go")
    assert "app.InstallPIIRedaction()" in _read(service, "cmd/server/main.go")


def test_go_logger_hook_ships_without_pii_redaction() -> None:
    service = _go_service({"middleware.pii_redaction": False})
    assert not (service / "internal" / "pii").exists()
    assert "func WrapLogHandler(" in _read(service, "internal/app/server.go")


def test_rfc_006_lists_the_go_middleware_at_tier_2() -> None:
    for name in ("pii_redaction", "response_cache"):
        assert FRAGMENT_REGISTRY[name].parity_tier == 2
    tiers = RFC_006.read_text(encoding="utf-8").split("### Tier 3")
    assert "`pii_redaction` (Python + Go today" in tiers[0]
    assert "`response_cache` (Python + Node + Go today" in tiers[0]
    assert "`pii_redaction`" not in tiers[1].split("## Enforcement")[0]
