"""``middleware.*`` options — request-path middleware on the backend.

Covers correlation IDs, rate limiting, security headers, PII-redacting
log filters, and opt-in HTTP response caching. Every option here is a
backend-only concern; the resolver expands the realising fragment per
target backend (Python / Node / Rust) where that backend supports it.
"""

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
            path="middleware.correlation_id",
            type=OptionType.ENUM,
            default="always-on",
            options=("always-on",),  # degenerate single-value enum
            summary="X-Request-ID ingress + ContextVar propagation.",
            description="""\
Every inbound request is tagged with an X-Request-ID header, the value
is stored in a ContextVar so any async task downstream sees it, and the
same ID is echoed back on the response.

This option is always-on — it has no off value. Index the
``correlation_id`` log field in your aggregator to trace a single
request end-to-end across services.

BACKENDS: python
ENDPOINTS: none — ambient context via service.observability.correlation""",
            category=FeatureCategory.OBSERVABILITY,
            enables={"always-on": ("correlation_id",)},
        )
    )

    api.add_option(
        Option(
            path="middleware.rate_limit",
            type=OptionType.BOOL,
            default=True,
            summary="Token-bucket limiter keyed by tenant or IP.",
            description="""\
Token-bucket rate limiter, keyed by tenant when authenticated or by
client IP otherwise. Protects downstream services from hot callers and
smooths burst traffic. Ships four first-class implementations with
matching knobs — Python (in-memory), Node (@fastify/rate-limit), Rust
(Axum tower layer), Go (net/http middleware, in-memory).

BACKENDS: python, node, rust, go
ENDPOINTS: returns 429 on limit breach; /health and /metrics skipped.
REQUIRES: nothing by default; set REDIS_URL to share state across replicas.""",
            category=FeatureCategory.RELIABILITY,
            enables={True: ("rate_limit",)},
        )
    )

    api.add_option(
        Option(
            path="middleware.security_headers",
            type=OptionType.BOOL,
            default=True,
            summary="CSP + XFO + HSTS + Referrer-Policy + Permissions-Policy.",
            description="""\
Attaches a conservative set of response headers (CSP, X-Frame-Options,
X-Content-Type-Options, Referrer-Policy, Permissions-Policy, and HSTS
on HTTPS responses) to every request. Turning this off is a deliberate
choice for intentionally-insecure demos.

BACKENDS: python, node, rust, go
ENDPOINTS: none — middleware decorates every response.""",
            category=FeatureCategory.RELIABILITY,
            enables={True: ("security_headers",)},
        )
    )

    api.add_option(
        Option(
            path="middleware.pii_redaction",
            type=OptionType.BOOL,
            default=True,
            summary="Logging filter that scrubs emails / tokens / API keys.",
            description="""\
A logging.Filter attached at startup that scrubs emails, bearer tokens,
common API-key shapes (sk-*, sk-ant-*, AIza*, hf_*), and
password=/api_key= value pairs from every log record before handlers
run. Helps satisfy GDPR / SOC2 log-hygiene requirements without
per-call-site discipline.

BACKENDS: python
ENDPOINTS: none — applies to logger output globally.""",
            category=FeatureCategory.RELIABILITY,
            enables={True: ("pii_redaction",)},
        )
    )

    api.add_option(
        Option(
            path="middleware.api_version",
            type=OptionType.BOOL,
            default=False,
            summary="RFC 8594 API-Version / Deprecation / Sunset / Link response headers.",
            description="""\
Adds an ``ApiVersionMiddleware`` that stamps every response with an
``API-Version`` header and, when configured, RFC 8594 ``Deprecation`` /
``Sunset`` / ``Link`` (rel=sunset) headers — so clients can detect a
version's lifecycle without out-of-band docs. Defaults to ``v1`` with no
deprecation; edit the ``add_middleware(ApiVersionMiddleware, ...)`` call to
announce a sunset.

BACKENDS: python
ENDPOINTS: none — middleware decorates every response.""",
            category=FeatureCategory.OBSERVABILITY,
            enables={True: ("api_version",)},
        )
    )

    api.add_option(
        Option(
            path="middleware.response_cache",
            type=OptionType.BOOL,
            default=False,
            summary="Opt-in HTTP response caching (Redis or in-memory).",
            description="""\
Wires a cache backend at startup so route handlers can decorate
themselves for server-side response caching. Python uses fastapi-cache2
with a Redis backend (falls back to in-memory if RESPONSE_CACHE_URL
isn't set); Node uses @fastify/caching. No blanket behavior change —
handlers opt in per-endpoint.

BACKENDS: python, node
ENDPOINTS: none — decorate existing routes with @cache(expire=N).
REQUIRES: RESPONSE_CACHE_URL pointing at Redis (recommended for prod).""",
            category=FeatureCategory.RELIABILITY,
            stability="beta",
            enables={True: ("response_cache",)},
        )
    )
