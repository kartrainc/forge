"""Wire the in-process dev issuer into the application's auth guard.

The ``in_memory`` auth provider ships this installer in place of the
Gatekeeper sidecar. :func:`install_in_memory_auth` is injected into the
application factory at ``FORGE:APP_POST_CONFIGURE`` — i.e. *before*
``AppLifecycle.bootstrap`` runs — so it can:

1. construct the single process-wide :class:`InMemoryIssuer` and stash it on
   ``app.state`` for the ``/dev/auth/*`` routes to reach, and
2. redirect ``AppLifecycle.bootstrap``'s ``build_auth_guard(...)`` call to the
   in-memory variant, so the guard that ends up on ``app.state`` verifies the
   exact tokens this issuer mints — no Gatekeeper, no Keycloak, no network.

The redirect is a deliberate, narrow rebinding of the ``build_auth_guard``
symbol that ``app.core.lifecycle`` imported at module load. It is the seam
that lets the issuer-agnostic SDK + middleware (shipped by
``auth.mode=generate``) stay byte-identical while only the *issuer* changes.
"""

from __future__ import annotations

import os

from fastapi import FastAPI

import app.core.lifecycle as _lifecycle
from app.core.config import Settings
from app.security.in_memory_issuer import (
    InMemoryIssuer,
    build_in_memory_auth_bundle,
)

# ``app.state`` key under which the process-wide issuer is stored so the
# ``/dev/auth/*`` route module can mint tokens + serve JWKS.
ISSUER_STATE_KEY = "in_memory_issuer"

# Environments where the unauthenticated dev issuer is acceptable: those the
# service's own production guards (SecurityConfig) exempt, as the Go provider.
# Anything else, ci and an unset ENV (which defaults to "production")
# included, is refused.
_DEV_ENVS = frozenset({"development", "dev", "test", "testing", "local"})


class InMemoryAuthInProductionError(RuntimeError):
    """Raised at startup when the in_memory provider is installed under a
    production posture — its ``/dev/auth/token`` route mints arbitrary
    identities with no authentication."""


def _refuse_in_production() -> None:
    """Fail closed if the unauthenticated in-memory issuer would run in prod.

    The ``in_memory`` provider exposes ``POST /dev/auth/token``, which mints a
    signed token for ANY subject / roles / tenant with no authentication —
    catastrophic outside development. The provider's docs have long claimed it
    "refuses to start in a production posture"; this is that control. ENV is
    read at boot (unset → ``production`` → refused), so a stray prod deploy
    crashes loudly instead of silently exposing token minting.
    """
    env = os.environ.get("ENV", os.environ.get("ENVIRONMENT", "production")).strip().lower()
    if env not in _DEV_ENVS:
        raise InMemoryAuthInProductionError(
            f"auth.provider=in_memory refuses to start in env={env!r}: the "
            f"/dev/auth/token route mints arbitrary identity tokens without "
            f"authentication. Use auth.provider=gatekeeper or oidc_generic in "
            f"production, or set ENV to a development posture for local use."
        )


def get_issuer(app: FastAPI) -> InMemoryIssuer:
    """Return the issuer installed on ``app.state`` (raises if absent)."""
    issuer = getattr(app.state, ISSUER_STATE_KEY, None)
    if issuer is None:
        raise RuntimeError(
            "In-memory issuer not installed. Call install_in_memory_auth(app) "
            "in the application factory before AppLifecycle.bootstrap()."
        )
    return issuer


def install_in_memory_auth(app: FastAPI, settings: Settings) -> None:
    """Install the in-process dev issuer and redirect the auth-guard builder.

    Constructs the :class:`InMemoryIssuer` (its audience matches the service's
    configured token audience so minted tokens pass ``aud`` verification),
    stores it on ``app.state``, then rebinds ``app.core.lifecycle``'s
    ``build_auth_guard`` to the in-memory bundle builder for this process.

    Refuses to install under a production posture (see
    :func:`_refuse_in_production`) — the dev issuer is unauthenticated.
    """
    _refuse_in_production()
    auth_config = settings.security.auth
    issuer = InMemoryIssuer(
        audience=auth_config.audience or "in-memory-dev",
        tenant_id_claim=auth_config.tenant_id_claim,
    )
    setattr(app.state, ISSUER_STATE_KEY, issuer)

    def _build_in_memory(config, **_kwargs):  # type: ignore[no-untyped-def]
        return build_in_memory_auth_bundle(config, issuer)

    _lifecycle.build_auth_guard = _build_in_memory
