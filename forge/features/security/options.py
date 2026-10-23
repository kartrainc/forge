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

FRONTENDS: vue (Svelte, Flutter and plugin frontends are served without it)
CUSTOMISE: add the origins your frontend calls to ``connect-src`` in nginx.conf.""",
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
            summary="GitHub Actions workflow emitting a CycloneDX SBOM + pip-audit report.",
            description="""\
Adds ``.github/workflows/sbom.yml`` that generates a CycloneDX SBOM on
every push and runs pip-audit weekly. Artifacts are uploaded so SBOM
attestation and vulnerability disclosure happens as part of normal CI.

BACKENDS: python
DEPENDENCY: none runtime; CI installs cyclonedx-bom + pip-audit.""",
            category=FeatureCategory.OBSERVABILITY,
            enables={True: ("security_sbom",)},
        )
    )
