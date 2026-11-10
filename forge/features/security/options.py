"""``security.*`` options — the frontend's content-security policy + CycloneDX SBOM workflow."""

from __future__ import annotations

from forge.api import ForgeAPI
from forge.options._registry import (
    FeatureCategory,
    Option,
    OptionType,
)


def register_all(api: ForgeAPI) -> None:
    api.add_option(
        Option(
            path="security.csp",
            type=OptionType.BOOL,
            default=True,
            summary="Nonce-based Content-Security-Policy for the Vue frontend, served by nginx.",
            description="""\
The frontend's nginx configuration (``apps/<frontend>/nginx.conf`` and the Helm
chart's default server block) sends a strict Content-Security-Policy with
``index.html``: no ``'unsafe-inline'`` or ``'unsafe-eval'``, scripts and
stylesheets admitted by a per-response nonce (``$request_id``, written over the
build's ``__CSP_NONCE__`` placeholder). Every response also carries
X-Content-Type-Options, X-Frame-Options, Referrer-Policy and
Permissions-Policy. Strict-Transport-Security is left to the TLS terminator.

FRONTENDS: vue without the AI chat, whose MCP panels frame remote origins
(Svelte, Flutter, plugin frontends and chat projects are served without it)
CUSTOMISE: add the origins your frontend calls, WebSocket ones included, to
``connect-src`` in nginx.conf.""",
            category=FeatureCategory.PLATFORM,
            # Read by the frontend renderers (forge.frontend_csp); no fragment.
            requires_backend=False,
        )
    )

    api.add_option(
        Option(
            path="security.sbom",
            type=OptionType.BOOL,
            default=False,
            summary="GitHub Actions workflow emitting a CycloneDX SBOM + vulnerability report per service.",
            description="""\
Adds ``.github/workflows/sbom.yml`` at the repository root. On every push,
pull request and weekly, it writes a CycloneDX SBOM and a vulnerability
report for each Python service (cyclonedx-py + pip-audit over the locked
environment) and each Go service (cyclonedx-gomod + govulncheck, on the Go
release its go.mod names), and uploads them as the ``sbom-python`` and
``sbom-go-<service>`` artifacts. A failed sync, SBOM or audit is a warning
annotation, never a failed run; the generated CI's own audit steps gate the
build.

BACKENDS: python, go
DEPENDENCY: none runtime; CI runs cyclonedx-bom, pip-audit, cyclonedx-gomod
and govulncheck.""",
            category=FeatureCategory.OBSERVABILITY,
            enables={True: ("security_sbom",)},
        )
    )
