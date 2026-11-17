"""Circuit breaker middleware for outbound HTTP calls.

Wraps downstream clients (LLM, vector store, auth) so a flaky dependency
doesn't propagate failures through to every request. Uses ``purgatory``
(the async circuit-breaker library) to track failure rates per host.

Usage::

    from app.core.circuit_breaker import breaker_registry

    async with await breaker_registry.get_breaker("openai"):
        response = await openai_client.chat(...)

Configuration via env:

    CIRCUIT_BREAKER_THRESHOLD=5          # consecutive failures before opening
    CIRCUIT_BREAKER_RESET_TIMEOUT=30     # seconds before half-open probe
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from purgatory import AsyncCircuitBreakerFactory


def build_breaker_registry() -> "AsyncCircuitBreakerFactory":
    """Construct an AsyncCircuitBreakerFactory with sane defaults."""
    from purgatory import AsyncCircuitBreakerFactory

    threshold = int(os.getenv("CIRCUIT_BREAKER_THRESHOLD", "5"))
    reset = int(os.getenv("CIRCUIT_BREAKER_RESET_TIMEOUT", "30"))
    return AsyncCircuitBreakerFactory(
        default_threshold=threshold,
        default_ttl=reset,
    )


# Module-level singleton; import from app startup.
breaker_registry = build_breaker_registry()
