"""Pure, deterministic computation of the multi-service platform synthesis.

The heart of Phase 4. :func:`compute_platform_synthesis` walks the resolved
plan + the project's backends and computes a single in-memory
:class:`PlatformSynthesis` object describing the service-to-service (S2S) auth
graph: one :class:`ServiceClient` per backend (client id / deterministic
secret / audiences-with-scopes / inter-service URL) plus the shared OIDC
issuer, realm, internal audience, and event-bus wiring.

Determinism is the design constraint: no clock, no randomness, no I/O, sorted
iteration everywhere. Secrets are HMAC-derived from the project slug + backend
name so the same config always yields the same registry — reproducible across
runs (golden-stable when later rendered) and idempotent for ``forge --update``
(re-derivation reproduces the same plaintext → no secret rotation churn).

P4.1 ships the computation only; the renderers do not consume this object yet
(that is P4.2), so enabling ``auth.service_discovery`` does not change any
generated file. Note that secret *hashing* (argon2) and secret emission are a
P4.2 concern — argon2's PasswordHasher does not support a fixed salt, so this
module intentionally carries the deterministic *plaintext* only.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from forge.capability_resolver import ResolvedPlan
    from forge.config import ProjectConfig

# Shared platform invariants. The gatekeeper is the sole token issuer across
# every generated service (the sole-issuer invariant); ``forge-services`` is the
# internal S2S audience. The realm is the project's own
# (``forge.config._topology.project_keycloak_realm``).
_DEFAULT_ISSUER = "http://gatekeeper:5000"
_DEFAULT_INTERNAL_AUDIENCE = "forge-services"
_TOKEN_ENDPOINT = f"{_DEFAULT_ISSUER}/auth/token"

# The shared database name provisioned for the postgres LISTEN/NOTIFY event bus.
_EVENT_BUS_DB = "events"

# HMAC message constant for S2S-secret derivation. The per-service key
# (project_slug + ":" + name) varies; this domain-separation tag does not.
_S2S_SECRET_MSG = b"forge-s2s"


def _derive_secret(project_slug: str, backend_name: str) -> str:
    """Derive a deterministic dev S2S secret for ``backend_name``.

    ``hmac_sha256(key=(project_slug + ":" + name), msg=b"forge-s2s")`` truncated
    to the first 32 hex chars. Deterministic by construction: the same config
    always produces the same secret (golden-stable once rendered, and
    rotation-free across ``forge --update``).

    These are *dev-only* secrets — deterministic-by-design means they are not
    cryptographically unpredictable across projects with a known slug. Real
    deployments rotate them; that hardening lands in a later phase.
    """
    key = f"{project_slug}:{backend_name}".encode()
    return hmac.new(key, _S2S_SECRET_MSG, hashlib.sha256).hexdigest()[:32]


@dataclass(frozen=True)
class ServiceClient:
    """The synthesized S2S identity for one backend.

    * ``name`` — the backend name (e.g. ``"orders"``).
    * ``client_id`` — ``f"svc-{name}"``; the OIDC/gatekeeper client id.
    * ``secret`` — the deterministic dev plaintext S2S secret (see
      :func:`_derive_secret`). Secret *hashing* is a P4.2 renderer concern.
    * ``audiences`` — ``callee_client_id -> sorted scopes``. One entry per
      ``depends_on`` edge: the scopes this service may request when calling
      that callee (default ``{callee:read, callee:write}``).
    * ``internal_url`` — ``f"http://{name}:{server_port}"``; the in-cluster
      base URL other services use to reach this one.
    """

    name: str
    client_id: str
    secret: str
    audiences: dict[str, list[str]]
    internal_url: str


@dataclass(frozen=True)
class PlatformSynthesis:
    """The computed cross-service platform graph for a multi-service project.

    Holds the per-service :class:`ServiceClient` tuple plus the shared identity
    invariants (realm / issuer / internal audience) and the event-bus wiring.
    :meth:`env_for` projects this into the per-backend inter-service env block
    the docker/compose renderer will consume in P4.2.
    """

    clients: tuple[ServiceClient, ...]
    realm: str
    issuer: str
    internal_audience: str
    event_bus: str
    event_bus_db: str | None

    def _client_for(self, name: str) -> ServiceClient | None:
        for client in self.clients:
            if client.name == name:
                return client
        return None

    def _client_by_id(self, client_id: str) -> ServiceClient | None:
        for client in self.clients:
            if client.client_id == client_id:
                return client
        return None

    def env_for(self, name: str) -> dict[str, str]:
        """Return the S2S / inter-service env block for backend ``name``.

        The block this backend needs to (a) mint its own S2S tokens against the
        gatekeeper and (b) reach each of its declared dependencies:

        * ``GATEKEEPER_CLIENT_ID`` / ``GATEKEEPER_CLIENT_SECRET`` — this
          service's own credentials.
        * ``GATEKEEPER_TOKEN_ENDPOINT`` — the shared mint endpoint.
        * ``INTERNAL_SERVICE_URL_<UPPER>`` — one per dependency, the callee's
          in-cluster URL (``UPPER`` is the callee name upper-cased with ``-``
          mapped to ``_``).
        * ``APP__EVENTS__BUS_URL`` — present only when the event bus is on.

        Returns an empty dict when ``name`` is not a synthesized service.
        Keys are insertion-ordered: own credentials, then dependency URLs
        sorted by callee name, then the optional event-bus URL — deterministic
        for golden stability.
        """
        client = self._client_for(name)
        if client is None:
            return {}
        env: dict[str, str] = {
            "GATEKEEPER_CLIENT_ID": client.client_id,
            "GATEKEEPER_CLIENT_SECRET": client.secret,
            "GATEKEEPER_TOKEN_ENDPOINT": _TOKEN_ENDPOINT,
        }
        # One INTERNAL_SERVICE_URL_* per declared dependency, sorted by the
        # callee's service name for deterministic ordering.
        for callee_client_id in sorted(client.audiences):
            callee = self._client_by_id(callee_client_id)
            if callee is None:
                continue
            key = f"INTERNAL_SERVICE_URL_{callee.name.upper().replace('-', '_')}"
            env[key] = callee.internal_url
        if self.event_bus != "none" and self.event_bus_db is not None:
            env["APP__EVENTS__BUS_URL"] = (
                f"postgresql://postgres:postgres@postgres:5432/{self.event_bus_db}"
            )
        return env


def synthesis_active(config: ProjectConfig, option_values: Mapping[str, object]) -> bool:
    """Whether :func:`compute_platform_synthesis` computes one for ``config``.

    ``auth.service_discovery`` on, more than one backend, and the gatekeeper as
    the RESOLVED (coerced) ``auth.provider``: the resolver coerces it to
    ``none`` whenever the gatekeeper stack isn't generated (``include_keycloak=
    False`` / ``auth.mode!=generate``), and a synthesis there would emit an
    orphan ``service_registry.yaml`` + ``GATEKEEPER_*`` env into a project that
    ships no gatekeeper/realm.
    """
    return (
        bool(option_values.get("auth.service_discovery"))
        and len(config.backends) > 1
        and option_values.get("auth.provider") == "gatekeeper"
    )


def compute_platform_synthesis(
    config: ProjectConfig,
    plan: ResolvedPlan,
) -> PlatformSynthesis | None:
    """Compute the platform synthesis, or ``None`` when it is not active.

    Active iff ``auth.service_discovery`` is on AND the project has more than
    one backend. Otherwise returns ``None`` (single-service projects + the
    feature-off default), so downstream renderers stay byte-identical.

    The computation is pure and deterministic:

    * one :class:`ServiceClient` per backend (project order);
    * ``client_id = f"svc-{name}"``;
    * a deterministic dev ``secret`` (see :func:`_derive_secret`);
    * ``audiences`` from ``depends_on`` — each edge ``bc -> callee`` adds the
      callee's ``svc-{callee}`` client id with sorted scopes
      ``{callee:read, callee:write}``;
    * ``internal_url`` from ``server_port``;
    * shared realm / issuer / internal audience;
    * ``event_bus`` + ``event_bus_db`` from ``infrastructure.event_bus``.

    Graph-membership / activation validation (a depends_on target must be a real
    backend; service_discovery needs >1 backend; cycles warn) lives in
    ``ProjectConfig.validate``; by the time this runs the graph is well-formed,
    so unknown ``depends_on`` targets are skipped defensively rather than
    re-validated.
    """
    # Defence-in-depth that holds even when ProjectConfig.validate is bypassed
    # (headless / matrix callers).
    if not synthesis_active(config, plan.option_values):
        return None

    from forge.config._topology import project_keycloak_realm  # noqa: PLC0415

    known_names = {bc.name for bc in config.backends}

    event_bus = str(plan.option_values.get("infrastructure.event_bus", "none"))
    event_bus_db = _EVENT_BUS_DB if event_bus == "postgres_notify" else None

    clients: list[ServiceClient] = []
    for bc in config.backends:
        audiences: dict[str, list[str]] = {}
        for callee in bc.depends_on:
            # Defensive: ProjectConfig.validate already rejects unknown targets.
            if callee not in known_names or callee == bc.name:
                continue
            callee_client_id = f"svc-{callee}"
            audiences[callee_client_id] = sorted({f"{callee}:read", f"{callee}:write"})
        clients.append(
            ServiceClient(
                name=bc.name,
                client_id=f"svc-{bc.name}",
                secret=_derive_secret(config.project_slug, bc.name),
                audiences=audiences,
                internal_url=f"http://{bc.name}:{bc.server_port}",
            )
        )

    return PlatformSynthesis(
        clients=tuple(clients),
        realm=project_keycloak_realm(config),
        issuer=_DEFAULT_ISSUER,
        internal_audience=_DEFAULT_INTERNAL_AUDIENCE,
        event_bus=event_bus,
        event_bus_db=event_bus_db,
    )
