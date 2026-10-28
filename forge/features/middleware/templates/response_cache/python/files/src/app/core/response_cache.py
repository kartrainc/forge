"""Response-cache wiring for fastapi-cache2.

Initializes the cache backend at lifecycle startup. Route handlers pick it
up via the ``@cache(expire=N)`` decorator from ``fastapi_cache.decorator``
— no further integration needed.

Backend selection:
  - ``RESPONSE_CACHE_URL`` set and reachable → Redis backend.
  - Otherwise → in-memory backend (per-process; fine for dev, not for
    multi-replica prod).
"""

from __future__ import annotations

import logging
import os
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)


def _redacted(url: str) -> str:
    """``url`` with its password masked, for logs."""
    parts = urlsplit(url)
    if parts.password is None:
        return url
    userinfo, host = parts.netloc.rsplit("@", 1)
    user = userinfo.split(":", 1)[0]
    return parts._replace(netloc=f"{user}:***@{host}").geturl()


def setup_response_cache() -> None:
    """Idempotent — safe to call multiple times; fastapi-cache2 dedupes."""
    try:
        from fastapi_cache import FastAPICache  # type: ignore
    except ImportError:
        logger.warning(
            "response_cache feature enabled but `fastapi-cache2` is not installed; "
            "run `uv sync` to pull it in",
        )
        return

    url = (os.environ.get("RESPONSE_CACHE_URL") or "").strip()
    prefix = os.environ.get("RESPONSE_CACHE_PREFIX", "forge:cache")

    if url:
        try:
            from fastapi_cache.backends.redis import RedisBackend  # type: ignore
            from redis.asyncio import from_url  # type: ignore

            client = from_url(url, encoding="utf-8", decode_responses=True)
            FastAPICache.init(RedisBackend(client), prefix=prefix)
            logger.info("response cache initialized (redis @ %s)", _redacted(url))
            return
        except ImportError as e:
            logger.warning("redis backend unavailable (%s); falling back to in-memory", e)
        except Exception as e:  # noqa: BLE001
            logger.warning("redis cache init failed (%s); falling back to in-memory", e)

    try:
        from fastapi_cache.backends.inmemory import InMemoryBackend  # type: ignore
    except ImportError:
        logger.warning("fastapi-cache2 in-memory backend unavailable; cache disabled")
        return

    FastAPICache.init(InMemoryBackend(), prefix=prefix)
    logger.info("response cache initialized (in-memory)")
