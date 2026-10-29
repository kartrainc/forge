"""Regression: generated backend-runtime fixes (audit #8, #13, #14).

Static guards over the templates; behavioural confirmation is the cargo/vitest
build + the matrix smoke lane (runtime). Each asserts the fix is present so a
revert is caught at PR time.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_RUST = _ROOT / "forge/templates/services/rust-service-template/template/src"
_NODE_APP = _ROOT / "forge/templates/services/node-service-template/template/src/app.ts.jinja"
_NODE_RL = _ROOT / "forge/features/middleware/templates/rate_limit/node/inject.yaml"
_NODE_HEALTH = (
    _ROOT
    / "forge/features/observability/templates/enhanced_health/node/files/src/services"
    / "health-checks.service.ts"
)


def test_rust_serve_wires_connect_info() -> None:
    # #8: without ConnectInfo the rate limiter keys every request on "anonymous".
    main_rs = (_RUST / "main.rs").read_text(encoding="utf-8")
    assert "into_make_service_with_connect_info::<SocketAddr>()" in main_rs


def test_rust_pagination_rejects_out_of_range() -> None:
    # #13: negative limit/skip must 422, not 500 (Postgres LIMIT/OFFSET error).
    repo = (_RUST / "data/repositories/item_repository.rs").read_text(encoding="utf-8")
    assert "AppError::Validation" in repo
    assert "skip < 0" in repo and "limit < 1" in repo


def test_node_rate_limit_trustproxy_and_keygen() -> None:
    # #14: trustProxy so req.ip reflects XFF behind Traefik; per-tenant/IP key.
    app = _NODE_APP.read_text(encoding="utf-8")
    rl = _NODE_RL.read_text(encoding="utf-8")
    assert "trustProxy" in app
    assert "keyGenerator" in rl
    assert "tenant:" in rl


def test_node_deep_health_redis_check_is_bounded() -> None:
    # The client's default reconnects without end: an unreachable Redis held /health/deep open
    # and left one reconnecting client behind per request.
    checks = _NODE_HEALTH.read_text(encoding="utf-8")
    assert "reconnectStrategy: false" in checks
    assert "connectTimeout: 2000" in checks
    assert "client.destroy()" in checks
