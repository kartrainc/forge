# src/app/gatekeeper/helpers.py
"""
Shared helpers used across the Gatekeeper routes.
"""

from __future__ import annotations

import hashlib
import logging
import urllib.parse
from typing import TypedDict

from fastapi import Response

from app.gatekeeper.config import get_settings


class RealmAccess(TypedDict, total=False):
    roles: list[str]


class JWTPayload(TypedDict, total=False):
    """Shape of the decoded Keycloak JWT access token."""

    sub: str
    email: str
    aud: str
    exp: int
    realm_access: RealmAccess


logger = logging.getLogger(__name__)


# ── Tenant extraction ──────────────────────────────────────────────────────


def extract_tenant(forwarded_host: str | None, host: str | None = None) -> str:
    """
    Derive the tenant ID from the ``X-Forwarded-Host`` (preferred) or
    ``Host`` header.

    Strategy: take the *first* subdomain label.
    ``tenantA.example.com`` → ``tenantA``

    Raises
    ------
    ValueError
        When no usable host header is present.
    """
    raw = forwarded_host or host
    if not raw:
        raise ValueError("Cannot resolve tenant: no Host or X-Forwarded-Host header")

    # Strip port if present (e.g. "tenantA.example.com:8080")
    hostname = raw.split(":")[0]
    parts = hostname.split(".")

    if len(parts) < 2:
        # Single-label hostname (e.g. "localhost") — treat whole name as tenant
        return hostname

    return parts[0]


# ── Login URL construction ─────────────────────────────────────────────────


def public_realm_url(tenant: str, issuer_url: str | None = None) -> str:
    """The realm URL as browsers reach Keycloak, which is also the issuer.

    ``KEYCLOAK_PUBLIC_BASE_URL`` takes the place of ``KEYCLOAK_BASE_URL`` at
    the start of the realm URL; an issuer elsewhere is kept as it is.
    """
    cfg = get_settings()
    realm_url = issuer_url or f"{cfg.keycloak_base_url}/{tenant}"
    internal = cfg.keycloak_base_url.rstrip("/")
    public = cfg.keycloak_public_base_url.rstrip("/")
    if public and realm_url.startswith(f"{internal}/"):
        return public + realm_url[len(internal) :]
    return realm_url


def build_login_url(
    tenant: str,
    redirect_uri: str,
    state: str,
    *,
    issuer_url: str | None = None,
    client_id: str | None = None,
    nonce: str | None = None,
    code_challenge: str | None = None,
    code_challenge_method: str | None = None,
    scope: str = "openid email profile",
    idp_hint: str | None = None,
) -> str:
    """
    Construct the Keycloak Authorization Endpoint URL for the given tenant.

    Parameters
    ----------
    tenant:
        Realm slug.
    redirect_uri:
        The ``/callback`` URL the user should be sent back to.
    state:
        Opaque, high-entropy value forwarded through the OIDC flow. It is
        the server-side lookup key for the bound-state envelope (CSRF
        defense); the originally-requested return URI lives inside that
        envelope, not here.
    issuer_url:
        Per-tenant issuer URL from TMS.  Falls back to static config; the
        browser is sent to its :func:`public_realm_url`.
    client_id:
        Per-tenant OIDC client ID.  Falls back to static config.
    nonce:
        OIDC ``nonce`` (replay binding). Echoed back inside the
        ``id_token`` and verified on ``/callback``. Omitted from the URL
        when ``None`` (keeps callers that have not opted in unchanged).
    code_challenge:
        PKCE ``code_challenge`` derived from a per-flow ``code_verifier``.
        Sent with ``code_challenge_method`` so the IdP binds the issued
        code to the verifier. Omitted when ``None``.
    code_challenge_method:
        PKCE transform — callers pass ``"S256"``. Defaults to ``None`` and is
        only emitted when **both** ``code_challenge`` and this method are
        provided (so a challenge is never sent without a declared method, and
        a method is never sent without a challenge).
    scope:
        Requested scopes; ``organizations.login_scope`` adds the
        ``organization`` scope when the tenant comes from organizations.
    idp_hint:
        Keycloak ``kc_idp_hint``: send the user straight to that identity
        provider (an organization's IdP, for instance). Omitted when ``None``.
    """
    cfg = get_settings()
    base_url = public_realm_url(tenant, issuer_url)
    cid = client_id or cfg.gatekeeper_client_id
    base = f"{base_url}/protocol/openid-connect/auth"
    params_dict: dict[str, str] = {
        "response_type": "code",
        "client_id": cid,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
    }
    if idp_hint is not None:
        params_dict["kc_idp_hint"] = idp_hint
    if nonce is not None:
        params_dict["nonce"] = nonce
    if code_challenge is not None and code_challenge_method is not None:
        params_dict["code_challenge"] = code_challenge
        params_dict["code_challenge_method"] = code_challenge_method
    params = urllib.parse.urlencode(params_dict)
    return f"{base}?{params}"


def build_logout_url(
    tenant: str,
    *,
    issuer_url: str | None = None,
    client_id: str | None = None,
    id_token_hint: str | None = None,
    post_logout_redirect_uri: str | None = None,
) -> str:
    """
    Construct the Keycloak end-session endpoint URL for the given tenant.

    With ``id_token_hint`` Keycloak ends the session without asking the user
    to confirm, and with ``client_id`` + ``post_logout_redirect_uri`` (one of
    the client's ``post.logout.redirect.uris``) it sends the browser back.
    """
    cfg = get_settings()
    base_url = public_realm_url(tenant, issuer_url)
    url = f"{base_url}/protocol/openid-connect/logout"
    params = {
        name: value
        for name, value in (
            ("client_id", client_id or cfg.gatekeeper_client_id),
            ("id_token_hint", id_token_hint),
            ("post_logout_redirect_uri", post_logout_redirect_uri),
        )
        if value
    }
    return f"{url}?{urllib.parse.urlencode(params)}"


# ── Response builders ──────────────────────────────────────────────────────


def create_success_response(
    payload: JWTPayload,
    tenant: str,
    *,
    access_token: str,
    new_access_token: str | None = None,
    new_refresh_token: str | None = None,  # noqa: ARG001 — kept for API compat
    extra_headers: dict[str, str] | None = None,
    internal_token: str | None = None,
) -> Response:
    """
    Build the HTTP 200 response returned to Traefik on successful
    authentication.

    Sets ``Authorization: Bearer <token>`` so Traefik forwards the
    verified access token to downstream services, where each service's
    :class:`platform_auth.AuthGuard` re-verifies it before extracting
    identity. The legacy ``X-Gatekeeper-*`` headers are gone — every
    downstream service now reads identity from the bearer token only.

    Post-BFF migration: this endpoint NO LONGER sets cookies on the
    response. Token refresh updates the server-side
    :class:`ServerSessionStore` row directly; the browser sees only
    the opaque ``tenant_session_id`` set at ``/callback`` time.
    ``new_access_token`` is still consumed to swap the bearer header
    so downstream sees a fresh token after refresh, but no
    ``Set-Cookie`` is emitted.

    When *internal_token* is provided it is set as the bearer on
    ``Authorization``. ``internal_token=None`` falls back to the
    Keycloak access token — only relevant in degenerate paths (mint
    cache miss + Redis down + ...); the steady-state path always
    receives a freshly-minted ES256 JWT from the per-jti cache.
    """
    response = Response(status_code=200)

    # ── Bearer forwarding ───────────────────────────────────────────────
    if internal_token is not None:
        response.headers["Authorization"] = f"Bearer {internal_token}"
    else:
        keycloak_token = new_access_token or access_token
        response.headers["Authorization"] = f"Bearer {keycloak_token}"

    # ── Extra headers (e.g. rate-limit) ─────────────────────────────────
    if extra_headers:
        for key, value in extra_headers.items():
            response.headers[key] = value

    return response


def create_machine_success_response(
    *,
    user_id: str,
    email: str,
    tenant: str,
    roles: list[str],
    extra_headers: dict[str, str] | None = None,
    internal_token: str | None = None,
) -> Response:
    """
    Build the HTTP 200 response for a machine (API-key) authentication.

    Produces the **exact same** ``X-Gatekeeper-*`` headers as the human
    track so downstream services are completely agnostic.

    When *internal_token* is provided it is set as the bearer on
    ``Authorization`` — same contract as the human (cookie) track. The
    legacy ``X-Gatekeeper-*`` headers are retained so backends not yet
    consuming the bearer keep working; they will be retired in a
    follow-up alongside the API-key→bearer machine-track migration.
    """
    response = Response(status_code=200)

    response.headers["X-Gatekeeper-User-Id"] = user_id
    response.headers["X-Gatekeeper-Email"] = email
    response.headers["X-Gatekeeper-Tenant"] = tenant
    response.headers["X-Gatekeeper-Roles"] = ",".join(roles)
    response.headers["X-Gatekeeper-Auth-Method"] = "api-key"

    if internal_token is not None:
        response.headers["Authorization"] = f"Bearer {internal_token}"

    if extra_headers:
        for key, value in extra_headers.items():
            response.headers[key] = value

    return response


def _set_session_id_cookie(
    response: Response,
    *,
    value: str,
    max_age: int | None = None,
) -> None:
    """Set the BFF session-id cookie (``tenant_session_id``).

    HttpOnly + SameSite=Lax + Secure. Lax (not Strict) so deep-linking
    from external tools (Slack / Jira / email) still surfaces a logged-
    in session. CSRF for authenticated mutations is mitigated at the
    API layer — the platform's typed FastAPI bodies require
    ``Content-Type: application/json``, which triggers CORS preflight
    and blocks cross-origin form-posts even with Lax cookies.
    """
    cfg = get_settings()
    response.set_cookie(
        key=cfg.session_id_cookie_name,
        value=value,
        httponly=True,
        samesite="lax",
        secure=cfg.cookie_secure,
        path="/",
        max_age=max_age,
    )


def _delete_session_id_cookie(response: Response) -> None:
    """Expire the BFF session-id cookie."""
    cfg = get_settings()
    response.delete_cookie(
        key=cfg.session_id_cookie_name,
        path="/",
        httponly=True,
        samesite="lax",
        secure=cfg.cookie_secure,
    )


# A sign-in is bound to the browser that started it: ``/auth/login`` sets a
# per-flow cookie whose value the bound-state envelope also holds, and
# ``/callback`` only accepts the flow's ``state`` alongside it. Without it,
# anyone could start a sign-in, stop before ``/callback`` and hand the link to
# a victim, whose browser would then hold the attacker's session. One cookie
# per flow (named after the state) so sign-ins in several tabs do not clash.
_LOGIN_COOKIE_PREFIX = "gk_login_"


def login_cookie_name(state: str) -> str:
    """Name of the cookie that binds the sign-in *state* to its browser."""
    return _LOGIN_COOKIE_PREFIX + hashlib.sha256(state.encode()).hexdigest()[:16]


def _set_login_cookie(response: Response, *, state: str, binding: str) -> None:
    """Bind the sign-in *state* to this browser until ``/callback``.

    Scoped to ``/callback`` and to the envelope's lifetime. SameSite=Lax is
    sent on the top-level redirect back from Keycloak.
    """
    cfg = get_settings()
    response.set_cookie(
        key=login_cookie_name(state),
        value=binding,
        httponly=True,
        samesite="lax",
        secure=cfg.cookie_secure,
        path="/callback",
        max_age=cfg.oidc_state_envelope_ttl_seconds,
    )


def _delete_login_cookie(response: Response, *, state: str) -> None:
    """Expire the cookie of a consumed sign-in."""
    cfg = get_settings()
    response.delete_cookie(
        key=login_cookie_name(state),
        path="/callback",
        httponly=True,
        samesite="lax",
        secure=cfg.cookie_secure,
    )


def validate_state(state: str | None) -> str:
    """
    Guard against open-redirect attacks on ``/callback``.

    ``state`` must be a **relative** path (starts with ``/``).
    Falls back to ``"/"`` when invalid or missing.
    """
    if not state or not state.startswith("/") or state.startswith("//"):
        logger.warning(
            "Invalid or missing state parameter (%r) — defaulting to '/'",
            state,
        )
        return "/"
    return state


def check_origin(
    *,
    method: str,
    origin: str | None,
    referer: str | None,
    expected_host: str,
) -> bool:
    """
    Defense-in-depth CSRF check for unsafe HTTP methods.

    ``SameSite=Lax`` + ``HttpOnly`` cookies block most cross-site state-changing
    requests in modern browsers, but the OAuth 2.0 BCP for browser apps and
    OWASP both call for an explicit second factor on top of SameSite. We
    require ``Origin`` (or ``Referer``) to match the request's own host on
    POST / PUT / PATCH / DELETE.

    Safe methods (GET / HEAD / OPTIONS) are not state-changing and are allowed
    without an Origin check.
    """
    if method.upper() in {"GET", "HEAD", "OPTIONS"}:
        return True

    candidate = origin or referer
    if not candidate:
        return False

    try:
        # Prefix with `//` so urlparse treats expected_host as netloc even when
        # it has no scheme (e.g. when it came straight from a host header).
        expected_hostname = urllib.parse.urlparse(f"//{expected_host}").hostname
        candidate_hostname = urllib.parse.urlparse(candidate).hostname
    except ValueError:
        return False

    if expected_hostname is None or candidate_hostname is None:
        return False
    return expected_hostname == candidate_hostname
