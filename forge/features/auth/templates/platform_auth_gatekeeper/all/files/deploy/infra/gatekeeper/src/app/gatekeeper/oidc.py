# src/app/gatekeeper/oidc.py
"""
Server-to-server helpers for the OIDC Authorization Code and Refresh flows
against Keycloak.
"""

from __future__ import annotations

import logging
from typing import Any

from app.gatekeeper.config import get_settings
from app.gatekeeper.http_client import get_http_client

logger = logging.getLogger(__name__)


def _token_endpoint(tenant: str, *, issuer_url: str | None = None) -> str:
    """Build the Keycloak token endpoint URL for *tenant*."""
    cfg = get_settings()
    base_url = issuer_url or f"{cfg.keycloak_base_url}/{tenant}"
    return f"{base_url}/protocol/openid-connect/token"


# NO @with_retry: the OAuth2 authorization_code grant is SINGLE-USE. If the POST
# reaches Keycloak and tokens are issued but the response read times out, a retry
# re-POSTs the same code, which Keycloak rejects with invalid_grant (400) — the
# original tokens are lost and the user can't log in. Non-idempotent token
# exchanges must not be retried. (audit #10)
async def exchange_code(
    tenant: str,
    code: str,
    redirect_uri: str,
    *,
    issuer_url: str | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
    code_verifier: str | None = None,
) -> dict[str, Any]:
    """
    Exchange an authorization *code* for tokens (access + refresh).

    Parameters
    ----------
    tenant:
        Realm slug.
    code:
        The ``code`` query-param received on ``/callback``.
    redirect_uri:
        The exact redirect_uri registered with Keycloak (must match).
    issuer_url:
        Per-tenant issuer URL from TMS.  Falls back to static config.
    client_id:
        Per-tenant OIDC client ID.  Falls back to static config.
    client_secret:
        Per-tenant OIDC client secret.  Falls back to static config.
    code_verifier:
        PKCE ``code_verifier`` (RFC 7636) matching the ``code_challenge``
        sent on the authorization request. When provided it is added to
        the token POST so Keycloak can verify the proof-of-possession.
        ``None`` preserves the pre-PKCE behaviour.

    Returns
    -------
    dict
        Keycloak's token response containing at least
        ``access_token`` and ``refresh_token``.
    """
    cfg = get_settings()
    url = _token_endpoint(tenant, issuer_url=issuer_url)

    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id or cfg.gatekeeper_client_id,
        "client_secret": client_secret or cfg.gatekeeper_client_secret,
        "redirect_uri": redirect_uri,
    }
    if code_verifier:
        payload["code_verifier"] = code_verifier

    logger.debug("Exchanging auth code for tenant=%s at %s", tenant, url)

    client = get_http_client()
    resp = await client.post(url, data=payload)
    resp.raise_for_status()
    return resp.json()


# NO @with_retry: refresh-token rotation makes this effectively single-use too —
# a retry after a read-timeout can present an already-rotated (now-invalid)
# refresh token. Don't retry non-idempotent token operations. (audit #10)
async def refresh_tokens(
    tenant: str,
    refresh_token: str,
    *,
    issuer_url: str | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
) -> dict[str, Any]:
    """
    Use a *refresh_token* to obtain a fresh pair of access + refresh tokens.

    Parameters
    ----------
    tenant:
        Realm slug.
    refresh_token:
        The refresh token stored in the user's cookie.
    issuer_url:
        Per-tenant issuer URL from TMS.  Falls back to static config.
    client_id:
        Per-tenant OIDC client ID.  Falls back to static config.
    client_secret:
        Per-tenant OIDC client secret.  Falls back to static config.

    Returns
    -------
    dict
        Keycloak's token response.
    """
    cfg = get_settings()
    url = _token_endpoint(tenant, issuer_url=issuer_url)

    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id or cfg.gatekeeper_client_id,
        "client_secret": client_secret or cfg.gatekeeper_client_secret,
    }

    logger.debug("Refreshing tokens for tenant=%s at %s", tenant, url)

    client = get_http_client()
    resp = await client.post(url, data=payload)
    resp.raise_for_status()
    return resp.json()


async def end_session(
    tenant: str,
    refresh_token: str,
    *,
    issuer_url: str | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
) -> None:
    """End the user's Keycloak session from the server (back-channel logout).

    POSTs the session's refresh token to the end-session endpoint with the
    client credentials. Keycloak then revokes the SSO session, so the next
    sign-in asks for the password again even if the browser never reaches
    the front-channel logout redirect.
    """
    cfg = get_settings()
    base_url = issuer_url or f"{cfg.keycloak_base_url}/{tenant}"
    payload = {
        "client_id": client_id or cfg.gatekeeper_client_id,
        "client_secret": client_secret or cfg.gatekeeper_client_secret,
        "refresh_token": refresh_token,
    }
    client = get_http_client()
    resp = await client.post(f"{base_url}/protocol/openid-connect/logout", data=payload)
    resp.raise_for_status()
