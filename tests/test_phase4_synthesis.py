"""Phase 4 (P4.0) — multi-service platform-synthesis seam + knobs.

P4.0 ships only the inert foundation: the new options/fields exist and default
to off, and the synthesis pass is a stub returning None — so generation stays
byte-identical (the golden snapshots are the contract; this file pins the
surface). Computation + emitted artifacts land in later Phase-4 sub-steps.
"""

from __future__ import annotations

import pytest

from forge.config import BackendConfig, BackendLanguage, ProjectConfig
from forge.options import OPTION_REGISTRY
from forge.options._registry import OptionType

# --- new option surface -----------------------------------------------------


def test_service_discovery_option_registered() -> None:
    opt = OPTION_REGISTRY["auth.service_discovery"]
    assert opt.type is OptionType.BOOL
    assert opt.default is False
    assert not opt.enables  # inert in P4.0 — no fragments yet


def test_event_bus_option_registered() -> None:
    opt = OPTION_REGISTRY["infrastructure.event_bus"]
    assert opt.type is OptionType.ENUM
    assert opt.default == "none"
    assert opt.options == ("none", "postgres_notify")
    assert not opt.enables  # inert in P4.0


# --- new config fields ------------------------------------------------------


def test_backend_depends_on_defaults_empty() -> None:
    bc = BackendConfig(name="api", project_name="p", language=BackendLanguage.PYTHON)
    assert bc.depends_on == []


def test_backend_depends_on_validates_names() -> None:
    BackendConfig(
        name="gateway",
        project_name="p",
        language=BackendLanguage.PYTHON,
        depends_on=["orders", "inventory"],
    ).validate()
    with pytest.raises(ValueError, match="depends_on entry 'Bad Name'"):
        BackendConfig(
            name="gateway",
            project_name="p",
            language=BackendLanguage.PYTHON,
            depends_on=["Bad Name"],
        ).validate()
    with pytest.raises(ValueError, match="cannot depend on itself"):
        BackendConfig(
            name="gateway",
            project_name="p",
            language=BackendLanguage.PYTHON,
            depends_on=["gateway"],
        ).validate()


def test_project_platform_template_defaults_none() -> None:
    cfg = ProjectConfig(
        project_name="p",
        backends=[BackendConfig(name="api", project_name="p", language=BackendLanguage.PYTHON)],
    )
    assert cfg.platform_template is None


# --- the synthesis seam is a no-op in P4.0 ----------------------------------


def test_synthesize_platform_stub_returns_none() -> None:
    from forge.capability_resolver import resolve
    from forge.generator import _synthesize_platform

    cfg = ProjectConfig(
        project_name="p",
        backends=[
            BackendConfig(
                name="gateway",
                project_name="p",
                language=BackendLanguage.PYTHON,
                depends_on=["orders"],
            ),
            BackendConfig(name="orders", project_name="p", language=BackendLanguage.PYTHON),
        ],
    )
    plan = resolve(cfg)
    # Even with depends_on edges declared, the synthesis is inert (returns None)
    # because auth.service_discovery defaults off — the byte-identical contract.
    assert _synthesize_platform(cfg, plan, project_root=None, quiet=True) is None  # type: ignore[arg-type]


# --- P4.1: the platform-synthesis COMPUTATION layer -------------------------

import hashlib  # noqa: E402
import hmac  # noqa: E402

from forge.capability_resolver import resolve  # noqa: E402
from forge.synthesis import (  # noqa: E402
    PlatformSynthesis,
    ServiceClient,
    compute_platform_synthesis,
)


def _multi_backend_config(
    *,
    service_discovery: bool,
    event_bus: str = "none",
) -> ProjectConfig:
    """A 3-backend shop: gateway -> {orders, inventory}."""
    opts: dict[str, object] = {"infrastructure.event_bus": event_bus}
    if service_discovery:
        opts["auth.service_discovery"] = True
    return ProjectConfig(
        project_name="Shop",
        # service_discovery resolves to a real gatekeeper only when keycloak is
        # on (the resolver coerces auth.provider->none otherwise), so a coherent
        # synthesis fixture must enable it.
        include_keycloak=True,
        backends=[
            BackendConfig(
                name="gateway",
                project_name="Shop",
                language=BackendLanguage.PYTHON,
                server_port=5010,
                depends_on=["orders", "inventory"],
            ),
            BackendConfig(
                name="orders",
                project_name="Shop",
                language=BackendLanguage.PYTHON,
                server_port=5020,
            ),
            BackendConfig(
                name="inventory",
                project_name="Shop",
                language=BackendLanguage.PYTHON,
                server_port=5030,
            ),
        ],
        options=opts,
    )


def test_compute_returns_none_when_service_discovery_off() -> None:
    cfg = _multi_backend_config(service_discovery=False)
    assert compute_platform_synthesis(cfg, resolve(cfg)) is None


def test_compute_returns_none_for_single_backend() -> None:
    cfg = ProjectConfig(
        project_name="Solo",
        backends=[BackendConfig(name="api", project_name="Solo", language=BackendLanguage.PYTHON)],
        options={"auth.service_discovery": True},
    )
    # NB: ProjectConfig.validate() would reject this combo; compute_* is the
    # pure layer and just no-ops on <2 backends.
    assert compute_platform_synthesis(cfg, resolve(cfg)) is None


def test_compute_returns_synthesis_when_active() -> None:
    cfg = _multi_backend_config(service_discovery=True)
    syn = compute_platform_synthesis(cfg, resolve(cfg))
    assert isinstance(syn, PlatformSynthesis)
    assert all(isinstance(c, ServiceClient) for c in syn.clients)
    # The project's realm, the same one compose, the gatekeeper and Helm use.
    assert syn.realm == "app"
    assert syn.issuer == "http://gatekeeper:5000"
    assert syn.internal_audience == "forge-services"


def test_client_ids_one_per_backend_in_order() -> None:
    cfg = _multi_backend_config(service_discovery=True)
    syn = compute_platform_synthesis(cfg, resolve(cfg))
    assert syn is not None
    assert [c.name for c in syn.clients] == ["gateway", "orders", "inventory"]
    assert [c.client_id for c in syn.clients] == ["svc-gateway", "svc-orders", "svc-inventory"]


def test_secret_is_deterministic_and_matches_exact_derivation() -> None:
    cfg = _multi_backend_config(service_discovery=True)
    syn = compute_platform_synthesis(cfg, resolve(cfg))
    assert syn is not None
    # Exact derivation: hmac_sha256(key=slug:name, msg=b"forge-s2s")[:32].
    for client in syn.clients:
        key = f"{cfg.project_slug}:{client.name}".encode()
        expected = hmac.new(key, b"forge-s2s", hashlib.sha256).hexdigest()[:32]
        assert client.secret == expected
        assert len(client.secret) == 32
    # Same config -> identical secrets (reproducible / golden-stable).
    syn2 = compute_platform_synthesis(cfg, resolve(cfg))
    assert syn2 is not None
    assert [c.secret for c in syn.clients] == [c.secret for c in syn2.clients]
    # Distinct services derive distinct secrets.
    secrets = [c.secret for c in syn.clients]
    assert len(set(secrets)) == len(secrets)


def test_audiences_derived_from_depends_on() -> None:
    cfg = _multi_backend_config(service_discovery=True)
    syn = compute_platform_synthesis(cfg, resolve(cfg))
    assert syn is not None
    gateway = next(c for c in syn.clients if c.name == "gateway")
    assert gateway.audiences == {
        "svc-orders": ["orders:read", "orders:write"],
        "svc-inventory": ["inventory:read", "inventory:write"],
    }
    # Leaf services declare no edges -> empty audiences.
    orders = next(c for c in syn.clients if c.name == "orders")
    assert orders.audiences == {}


def test_internal_urls_from_ports() -> None:
    cfg = _multi_backend_config(service_discovery=True)
    syn = compute_platform_synthesis(cfg, resolve(cfg))
    assert syn is not None
    urls = {c.name: c.internal_url for c in syn.clients}
    assert urls == {
        "gateway": "http://gateway:5010",
        "orders": "http://orders:5020",
        "inventory": "http://inventory:5030",
    }


def test_event_bus_wiring_none_vs_postgres_notify() -> None:
    off = _multi_backend_config(service_discovery=True, event_bus="none")
    syn_off = compute_platform_synthesis(off, resolve(off))
    assert syn_off is not None
    assert syn_off.event_bus == "none"
    assert syn_off.event_bus_db is None

    on = _multi_backend_config(service_discovery=True, event_bus="postgres_notify")
    syn_on = compute_platform_synthesis(on, resolve(on))
    assert syn_on is not None
    assert syn_on.event_bus == "postgres_notify"
    assert syn_on.event_bus_db == "events"


def test_env_for_returns_expected_keys() -> None:
    cfg = _multi_backend_config(service_discovery=True, event_bus="postgres_notify")
    syn = compute_platform_synthesis(cfg, resolve(cfg))
    assert syn is not None
    env = syn.env_for("gateway")
    assert env["GATEKEEPER_CLIENT_ID"] == "svc-gateway"
    assert env["GATEKEEPER_CLIENT_SECRET"] == next(
        c.secret for c in syn.clients if c.name == "gateway"
    )
    assert env["GATEKEEPER_TOKEN_ENDPOINT"] == "http://gatekeeper:5000/auth/token"
    # One INTERNAL_SERVICE_URL_* per dependency, callee URL.
    assert env["INTERNAL_SERVICE_URL_ORDERS"] == "http://orders:5020"
    assert env["INTERNAL_SERVICE_URL_INVENTORY"] == "http://inventory:5030"
    # Event bus URL present when the bus is on.
    assert env["APP__EVENTS__BUS_URL"] == (
        "postgresql://postgres:postgres@postgres:5432/events"
    )

    # A leaf service has its own creds + no dependency URLs.
    leaf_env = syn.env_for("orders")
    assert leaf_env["GATEKEEPER_CLIENT_ID"] == "svc-orders"
    assert not any(k.startswith("INTERNAL_SERVICE_URL_") for k in leaf_env)

    # No event-bus key when the bus is off.
    off = _multi_backend_config(service_discovery=True, event_bus="none")
    syn_off = compute_platform_synthesis(off, resolve(off))
    assert syn_off is not None
    assert "APP__EVENTS__BUS_URL" not in syn_off.env_for("gateway")

    # Unknown service -> empty env block.
    assert syn.env_for("does-not-exist") == {}


# --- P4.1: ProjectConfig graph + activation validation ----------------------


def test_validate_rejects_service_discovery_with_single_backend() -> None:
    cfg = ProjectConfig(
        project_name="Solo",
        backends=[BackendConfig(name="api", project_name="Solo", language=BackendLanguage.PYTHON)],
        options={"auth.service_discovery": True},
    )
    with pytest.raises(ValueError, match="requires more than one backend"):
        cfg.validate()


def test_validate_rejects_depends_on_unknown_backend() -> None:
    cfg = ProjectConfig(
        project_name="Shop",
        include_keycloak=True,
        backends=[
            BackendConfig(
                name="gateway",
                project_name="Shop",
                language=BackendLanguage.PYTHON,
                server_port=5010,
                depends_on=["ghost"],
            ),
            BackendConfig(
                name="orders",
                project_name="Shop",
                language=BackendLanguage.PYTHON,
                server_port=5020,
            ),
        ],
        options={"auth.service_discovery": True},
    )
    with pytest.raises(ValueError, match="depends_on 'ghost'"):
        cfg.validate()


def test_validate_warns_on_cycle_but_does_not_raise(caplog) -> None:
    import logging

    cfg = ProjectConfig(
        project_name="Shop",
        include_keycloak=True,
        backends=[
            BackendConfig(
                name="a",
                project_name="Shop",
                language=BackendLanguage.PYTHON,
                server_port=5010,
                depends_on=["b"],
            ),
            BackendConfig(
                name="b",
                project_name="Shop",
                language=BackendLanguage.PYTHON,
                server_port=5020,
                depends_on=["a"],
            ),
        ],
        options={"auth.service_discovery": True},
    )
    with caplog.at_level(logging.WARNING, logger="forge.config"):
        cfg.validate()  # must not raise
    assert any("cycle" in rec.message for rec in caplog.records)


def test_validate_rejects_service_discovery_without_keycloak() -> None:
    """Gap-1 guard: service_discovery without the gatekeeper stack fails fast.

    With keycloak off, the resolver coerces auth.provider->none, so synthesis
    would emit an orphan registry + GATEKEEPER_* env into a project with no
    gatekeeper. Validation must reject it up front.
    """
    cfg = ProjectConfig(
        project_name="Shop",
        include_keycloak=False,
        backends=[
            BackendConfig(
                name="gateway",
                project_name="Shop",
                language=BackendLanguage.PYTHON,
                server_port=5010,
                depends_on=["orders"],
            ),
            BackendConfig(
                name="orders",
                project_name="Shop",
                language=BackendLanguage.PYTHON,
                server_port=5020,
            ),
        ],
        options={"auth.service_discovery": True},
    )
    with pytest.raises(ValueError, match="include_keycloak=True"):
        cfg.validate()


def test_validate_rejects_event_bus_postgres_notify_without_database() -> None:
    """Gap-2 guard: postgres_notify event bus requires a database."""
    cfg = ProjectConfig(
        project_name="Shop",
        include_keycloak=True,
        backends=[
            BackendConfig(
                name="gateway",
                project_name="Shop",
                language=BackendLanguage.PYTHON,
                server_port=5010,
                depends_on=["orders"],
            ),
            BackendConfig(
                name="orders",
                project_name="Shop",
                language=BackendLanguage.PYTHON,
                server_port=5020,
            ),
        ],
        options={
            "auth.service_discovery": True,
            "infrastructure.event_bus": "postgres_notify",
            "database.mode": "none",
        },
    )
    with pytest.raises(ValueError, match="event_bus=postgres_notify requires a database"):
        cfg.validate()


def test_compute_self_disables_without_resolved_gatekeeper() -> None:
    """Gap-1 runtime defence: compute returns None when the resolved provider
    is not gatekeeper, even if service_discovery + >1 backend hold (e.g. a
    headless caller that bypassed validate with keycloak off)."""
    cfg = ProjectConfig(
        project_name="Shop",
        include_keycloak=False,  # -> resolver coerces auth.provider to none
        backends=[
            BackendConfig(
                name="gateway",
                project_name="Shop",
                language=BackendLanguage.PYTHON,
                server_port=5010,
                depends_on=["orders"],
            ),
            BackendConfig(
                name="orders",
                project_name="Shop",
                language=BackendLanguage.PYTHON,
                server_port=5020,
            ),
        ],
        options={"auth.service_discovery": True},
    )
    plan = resolve(cfg)
    assert plan.option_values.get("auth.provider") != "gatekeeper"
    assert compute_platform_synthesis(cfg, plan) is None


def test_validate_passes_for_acyclic_multi_service() -> None:
    cfg = _multi_backend_config(service_discovery=True)
    cfg.validate()  # gateway -> {orders, inventory} is a DAG; no raise
