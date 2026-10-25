"""HTTP middleware fragments — request-path cross-cutting concerns.

Order is significant: ``correlation_id`` is outermost (order=90) so its
context is set before any other middleware runs; ``security_headers``
(order=80) is below it; ``rate_limit`` (order=50) sits in the middle.

Fragment template trees ship from this package using absolute paths via
``Path(__file__).resolve().parent / "templates"`` — the same convention
plugin authors use (see ``docs/plugin-development.md``). The injector's
``_resolve_fragment_dir`` returns absolute paths verbatim, so built-in
features and plugin features flow through identical resolution code.
"""

from __future__ import annotations

from pathlib import Path

from forge.api import ForgeAPI
from forge.config import BackendLanguage
from forge.fragments._spec import Fragment, FragmentImplSpec
from forge.middleware_spec import MiddlewareSpec

_TEMPLATES = Path(__file__).resolve().parent / "templates"


def _impl(name: str, lang: str) -> str:
    return str(_TEMPLATES / name / lang)


def register_all(api: ForgeAPI) -> None:
    api.add_fragment(
        Fragment(
            name="correlation_id",
            order=90,  # outermost middleware — registers last, runs first
            # The worker variant ships no FastAPI app (no src/app/main.py), so
            # HTTP-shaped default-on fragments must skip it or generation
            # crashes with "Injection target not found".
            excluded_app_templates=("worker",),
            implementations={
                BackendLanguage.PYTHON: FragmentImplSpec(
                    fragment_dir=_impl("correlation_id", "python"),
                ),
            },
            # Epic K (1.1.0-alpha.1) — MiddlewareSpec replaces the
            # correlation_id/python/inject.yaml file. The files/ tree (the
            # actual CorrelationIdMiddleware class) still lives on disk; only
            # the import + app.add_middleware(...) ceremony is declarative now.
            middlewares=(
                MiddlewareSpec(
                    name="correlation_id",
                    backend=BackendLanguage.PYTHON,
                    order=90,
                    import_snippet=(
                        "from app.middleware.correlation import CorrelationIdMiddleware"
                    ),
                    register_snippet=(
                        "# Correlation ID (outermost — runs first, sets context for all inner middleware)\n"
                        "app.add_middleware(CorrelationIdMiddleware)"
                    ),
                ),
            ),
        )
    )

    api.add_fragment(
        Fragment(
            name="rate_limit",
            order=50,
            excluded_app_templates=("worker",),  # no HTTP surface to rate-limit
            implementations={
                BackendLanguage.PYTHON: FragmentImplSpec(
                    fragment_dir=_impl("rate_limit", "python")
                ),
                BackendLanguage.NODE: FragmentImplSpec(
                    fragment_dir=_impl("rate_limit", "node"),
                    dependencies=("@fastify/rate-limit@11.2.1",),
                ),
                BackendLanguage.RUST: FragmentImplSpec(fragment_dir=_impl("rate_limit", "rust")),
                BackendLanguage.GO: FragmentImplSpec(fragment_dir=_impl("rate_limit", "go")),
            },
        )
    )

    api.add_fragment(
        Fragment(
            name="security_headers",
            order=80,  # below correlation_id (90) so registers inside it
            excluded_app_templates=("worker",),  # no HTTP responses to decorate
            implementations={
                BackendLanguage.PYTHON: FragmentImplSpec(
                    fragment_dir=_impl("security_headers", "python"),
                ),
                BackendLanguage.NODE: FragmentImplSpec(
                    fragment_dir=_impl("security_headers", "node"),
                    dependencies=("@fastify/helmet@13.1.2",),
                ),
                BackendLanguage.RUST: FragmentImplSpec(
                    fragment_dir=_impl("security_headers", "rust"),
                ),
                BackendLanguage.GO: FragmentImplSpec(
                    fragment_dir=_impl("security_headers", "go"),
                ),
            },
        )
    )

    api.add_fragment(
        Fragment(
            name="api_version",
            order=70,  # below security_headers (80); only decorates response headers
            implementations={
                BackendLanguage.PYTHON: FragmentImplSpec(
                    fragment_dir=_impl("api_version", "python"),
                ),
            },
            middlewares=(
                MiddlewareSpec(
                    name="api_version",
                    backend=BackendLanguage.PYTHON,
                    order=70,
                    import_snippet="from app.middleware.api_version import ApiVersionMiddleware",
                    register_snippet=(
                        "# API version + RFC 8594 deprecation/sunset headers on every response\n"
                        'app.add_middleware(ApiVersionMiddleware, current_version="v1")'
                    ),
                ),
            ),
        )
    )

    api.add_fragment(
        Fragment(
            name="pii_redaction",
            excluded_app_templates=("worker",),  # middleware needs the FastAPI app
            implementations={
                BackendLanguage.PYTHON: FragmentImplSpec(
                    fragment_dir=_impl("pii_redaction", "python"),
                ),
                BackendLanguage.GO: FragmentImplSpec(
                    fragment_dir=_impl("pii_redaction", "go"),
                ),
            },
        )
    )

    api.add_fragment(
        Fragment(
            name="response_cache",
            capabilities=("redis",),
            implementations={
                BackendLanguage.PYTHON: FragmentImplSpec(
                    fragment_dir=_impl("response_cache", "python"),
                    dependencies=("fastapi-cache2>=0.2.2", "redis>=6.0.0"),
                    env_vars=(
                        ("RESPONSE_CACHE_URL", "redis://redis:6379/1"),
                        ("RESPONSE_CACHE_PREFIX", "forge:cache"),
                    ),
                ),
                BackendLanguage.NODE: FragmentImplSpec(
                    fragment_dir=_impl("response_cache", "node"),
                    dependencies=("@fastify/caching@9.0.4",),
                    env_vars=(("RESPONSE_CACHE_URL", "redis://redis:6379/1"),),
                ),
                BackendLanguage.GO: FragmentImplSpec(
                    fragment_dir=_impl("response_cache", "go"),
                    # miniredis backs the generated unit tests.
                    dependencies=(
                        "github.com/redis/go-redis/v9@v9.23.0",
                        "github.com/alicebob/miniredis/v2@v2.40.0",
                    ),
                    # An empty prefix keeps the per-service default
                    # "<service>:cache".
                    env_vars=(
                        ("RESPONSE_CACHE_URL", "redis://redis:6379/1"),
                        ("RESPONSE_CACHE_PREFIX", ""),
                    ),
                ),
            },
        )
    )
