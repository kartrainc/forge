"""Stdlib-only credential redaction helpers for the Gatekeeper logs (WS-2.6).

Kept deliberately dependency-free (no fastapi / starlette / redis) so the
logic is unit-testable in forge CI via importlib -- mirrors the MCP audit
module's "pure helper, importlib-loaded" pattern.

Three leaks this module closes:

* The access-log formatter records the request query string. The OIDC
  callback carries ``?code=...&state=...`` (and, on some flows, a
  ``refresh_token``); writing those verbatim drops live OAuth secrets into
  the access log. :func:`redact_query_params` masks a denylist of sensitive
  keys before they are logged.
* ``server_session`` logs an opaque session id on issue / delete / decrypt-
  failure. A session id IS the bearer credential, so a log reader holds a
  live session. :func:`session_fp` keeps a short prefix for correlation and
  never the full value.
* The Redis client logs the URL it connects to, which carries the password
  when Redis requires one. :func:`redact_url` masks it.

Denylist, not allowlist: a brand-new, non-sensitive query param still gets
logged (observability is the goal -- WS-10.3 builds dashboards over these
lines), only the known-sensitive keys are masked.
"""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REDACTED = "<redacted>"

#: Sensitive query-param keys, compared case-insensitively. Covers the OAuth
#: authorization-code grant secrets (``code``, ``state``), every token
#: flavour, and the generic credential params that occasionally ride a query
#: string. New, unlisted params are passed through unchanged.
SENSITIVE_QUERY_KEYS: frozenset[str] = frozenset(
    {
        "code",
        "state",
        "refresh_token",
        "access_token",
        "id_token",
        "token",
        "client_secret",
        "password",
        "authorization",
    }
)

#: How many leading characters of a session id to keep for log correlation.
_FP_PREFIX_LEN = 8


def _is_sensitive(key: str) -> bool:
    return key.lower() in SENSITIVE_QUERY_KEYS


def redact_query_params(params: Mapping[str, str] | str) -> dict[str, str] | str:
    """Redact sensitive values out of request query params before logging.

    Accepts either a mapping (e.g. ``dict(request.query_params)``) or a raw
    query string (``"code=abc&state=xyz"``) and returns the same shape with
    every sensitive value replaced by ``"<redacted>"``. Non-sensitive params
    pass through unchanged -- this is a denylist, so new query keys still log.

    Key matching is case-insensitive; the original key casing is preserved in
    the output so the logged field name still matches the wire.
    """
    if isinstance(params, str):
        # Preserve order and any repeated keys; keep blank values so the param
        # shape in the log matches the request. ``safe="<>"`` keeps the literal
        # ``<redacted>`` marker readable in the log instead of percent-encoding
        # it to ``%3Credacted%3E``.
        pairs = parse_qsl(params, keep_blank_values=True)
        redacted_pairs = [
            (key, REDACTED if _is_sensitive(key) else value) for key, value in pairs
        ]
        return urlencode(redacted_pairs, safe="<>")

    return {
        key: (REDACTED if _is_sensitive(key) else value)
        for key, value in params.items()
    }


def session_fp(session_id: str | None) -> str:
    """Return a short, log-safe fingerprint of an opaque session id.

    Keeps the first :data:`_FP_PREFIX_LEN` characters followed by ``"..."`` so
    operators can correlate log lines without the full bearer credential ever
    reaching the log. ``None`` / empty ids are handled without raising.

    The output never contains more than the leading prefix of the id, and
    never the full value. A non-string id (defensive: callers are typed
    ``str | None``) is coerced to ``str`` so logging can never raise.
    """
    if not session_id:
        return "-"
    if not isinstance(session_id, str):
        session_id = str(session_id)
    return session_id[:_FP_PREFIX_LEN] + "..."


def redact_url(url: str) -> str:
    """Return ``url`` with the password of its userinfo replaced by ``"<redacted>"``.

    A URL without a password is returned unchanged; one that cannot be parsed
    is replaced whole, so logging can never leak or raise.
    """
    try:
        parts = urlsplit(url)
        if parts.password is None:
            return url
    except ValueError:
        return REDACTED
    host = parts.netloc.rpartition("@")[2]
    return urlunsplit(
        parts._replace(netloc=f"{parts.username or ''}:{REDACTED}@{host}")
    )


__all__ = [
    "REDACTED",
    "SENSITIVE_QUERY_KEYS",
    "redact_query_params",
    "redact_url",
    "session_fp",
]
