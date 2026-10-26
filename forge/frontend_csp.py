"""The content-security policy a Vue frontend is served under (``security.csp``).

With the option on, the frontend's nginx configuration (``nginx.conf`` and the
Helm chart's default server block) sends the defence-in-depth headers of
:func:`security_headers` with every response, and the policy of
:func:`content_security_policy` with ``index.html``. The policy admits scripts
and stylesheets by a nonce: nginx uses ``$request_id`` (16 random bytes per
request, as hex) and writes it over :data:`NONCE_PLACEHOLDER`, which Vite
stamps on its own tags (``html.cspNonce``) and the app reads back from the
``csp-nonce`` meta tag for the ``<style>`` elements its UI kit inserts.

Only the Vue template is built for the policy, so Svelte, Flutter and plugin
frontends are served without it, and so is a Vue frontend with the AI chat:
its MCP panels load remote origins in frames, which the policy would block
(ADR-011). Strict-Transport-Security is left to
the TLS terminator in front of the frontend: nginx serves plain HTTP behind it,
and a browser keeps the header for as long as it says.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from forge.config import FrontendFramework

if TYPE_CHECKING:
    from forge.config import ProjectConfig

#: The option that turns the policy on.
OPTION = "security.csp"

#: What Vite stamps where the nonce goes; nginx replaces it per response.
NONCE_PLACEHOLDER = "__CSP_NONCE__"

#: The nginx variable that supplies each response's nonce.
NONCE_VARIABLE = "$request_id"

#: The Google Fonts hosts of the template's font link (stylesheet, font files).
_FONT_STYLESHEETS = "https://fonts.googleapis.com"
_FONT_FILES = "https://fonts.gstatic.com"


def csp_enabled(config: ProjectConfig) -> bool:
    """True when ``security.csp`` is on and the frontend is the Vue template without chat."""
    frontend = config.frontend
    if frontend is None or frontend.framework != FrontendFramework.VUE:
        return False
    if frontend.include_chat:
        return False
    if OPTION in config.options:
        return bool(config.options[OPTION])
    from forge.options._registry import get_option  # noqa: PLC0415

    option = get_option(OPTION)
    return bool(option.default) if option is not None else False


def _connect_sources(config: ProjectConfig) -> list[str]:
    """``'self'``, plus the external API's origin when the frontend calls one.

    No ``ws:`` or ``wss:``: the template opens no WebSocket (its event streams
    are ``fetch`` requests, which ``'self'`` admits), and either scheme alone
    admits every host. A project that opens one adds its origin in ``nginx.conf``.
    """
    from forge.variable_mapper import external_api_url  # noqa: PLC0415

    sources = ["'self'"]
    url = external_api_url(config)
    if url:
        parts = urlsplit(url)
        if parts.scheme and parts.netloc:
            sources.append(f"{parts.scheme}://{parts.netloc}")
    return sources


def content_security_policy(config: ProjectConfig) -> str:
    """The policy nginx sends with ``index.html``, or ``""`` when it sends none.

    ``'report-sample'`` makes a violation report carry the first characters of
    the blocked code, which tells two known upstream elements apart from a real
    violation (``forge.quality.csp``).
    """
    if not csp_enabled(config):
        return ""
    nonce = f"'nonce-{NONCE_VARIABLE}'"
    directives = [
        ("default-src", ["'self'"]),
        ("script-src", ["'self'", nonce, "'strict-dynamic'", "'report-sample'"]),
        ("style-src", ["'self'", nonce, "'report-sample'", _FONT_STYLESHEETS]),
        ("img-src", ["'self'", "data:", "blob:"]),
        ("font-src", ["'self'", "data:", _FONT_FILES]),
        ("connect-src", _connect_sources(config)),
        ("frame-ancestors", ["'none'"]),
        ("form-action", ["'self'"]),
        ("base-uri", ["'self'"]),
        ("object-src", ["'none'"]),
    ]
    return "; ".join(f"{name} {' '.join(sources)}" for name, sources in directives)


def security_headers(config: ProjectConfig) -> tuple[tuple[str, str], ...]:
    """The headers nginx sends with every response when the policy is on."""
    if not csp_enabled(config):
        return ()
    return (
        ("X-Content-Type-Options", "nosniff"),
        ("X-Frame-Options", "DENY"),
        ("Referrer-Policy", "strict-origin-when-cross-origin"),
        ("Permissions-Policy", "geolocation=(), camera=(), microphone=(), payment=()"),
    )
