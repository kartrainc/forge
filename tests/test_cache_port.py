"""Tests for the cache_port + memory/redis adapters (Pillar E.2).

The port is the generic K/V cache surface — distinct from the
``response_cache`` HTTP-shape middleware. Adapters ship for all three
built-in backends from day one (tier-1 from the start) per the Pillar
E.2 plan in ``deep-gliding-mccarthy.md``.

This file covers:

1. Fragment-registry shape (three fragments, dependency wiring,
   capability + dep declarations).
2. On-disk file shape (port + adapter files at conventional paths).
3. Inject.yaml well-formedness (one entry per fragment, hits the
   right marker per language).
4. Resolver dispatch (``reliability.cache=memory|redis|none`` produces
   the expected fragment set).
5. In-memory adapter behaviour — TTL eviction actually happens.
6. Distinctness from ``response_cache`` — ensures we haven't
   accidentally collided with the HTTP middleware fragment.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

from forge.capability_resolver import resolve
from forge.config import BackendConfig, BackendLanguage, ProjectConfig
from forge.fragments import FRAGMENT_REGISTRY

# -- fragment-registry shape --------------------------------------------------


def test_cache_port_covers_all_three_built_ins() -> None:
    """Tier-1 from the start: Python + Node + Rust ship together."""
    frag = FRAGMENT_REGISTRY["cache_port"]
    assert BackendLanguage.PYTHON in frag.implementations
    assert BackendLanguage.NODE in frag.implementations
    assert BackendLanguage.RUST in frag.implementations


def test_cache_memory_fragment_registered_tier1() -> None:
    assert "cache_memory" in FRAGMENT_REGISTRY
    frag = FRAGMENT_REGISTRY["cache_memory"]
    for lang in (BackendLanguage.PYTHON, BackendLanguage.NODE, BackendLanguage.RUST):
        assert lang in frag.implementations, f"cache_memory missing {lang.value} impl"
    # In-process LRU — no external service, so no ``redis`` capability.
    assert "redis" not in frag.capabilities


def test_cache_redis_fragment_registered_tier1_with_redis_capability() -> None:
    assert "cache_redis" in FRAGMENT_REGISTRY
    frag = FRAGMENT_REGISTRY["cache_redis"]
    for lang in (BackendLanguage.PYTHON, BackendLanguage.NODE, BackendLanguage.RUST):
        assert lang in frag.implementations, f"cache_redis missing {lang.value} impl"
    # Redis sidecar is provisioned via the ``redis`` capability flag,
    # matching how queue_redis / rate_limit_redis declare the same dep.
    assert "redis" in frag.capabilities


def test_cache_adapters_depend_on_port() -> None:
    """Adapters import the port type — without the port fragment the
    interface file would be missing at compile/type-check time."""
    for adapter in ("cache_memory", "cache_redis"):
        assert "cache_port" in FRAGMENT_REGISTRY[adapter].depends_on


def test_cache_port_rust_declares_own_deps() -> None:
    """The Rust trait declaration uses async_trait + serde_json +
    thiserror; landing the port without these deps would fail
    ``cargo check`` even before an adapter wires in."""
    frag = FRAGMENT_REGISTRY["cache_port"]
    impl = frag.implementations[BackendLanguage.RUST]
    deps_str = " ".join(impl.dependencies)
    for needed in ("async-trait", "serde_json", "thiserror"):
        assert needed in deps_str, f"cache_port/rust missing dep: {needed!r}"


def test_cache_redis_adapter_deps_per_language() -> None:
    """Each language pulls in the canonical Redis client for that
    ecosystem; ports are typed against the same wire format
    (JSON) so swapping clients later doesn't break callers."""
    frag = FRAGMENT_REGISTRY["cache_redis"]
    py_deps = " ".join(frag.implementations[BackendLanguage.PYTHON].dependencies)
    assert "redis" in py_deps
    node_deps = " ".join(frag.implementations[BackendLanguage.NODE].dependencies)
    assert "ioredis" in node_deps
    rust_deps = " ".join(frag.implementations[BackendLanguage.RUST].dependencies)
    assert "redis" in rust_deps


def test_cache_redis_env_var_dedicated_db() -> None:
    """Cache traffic defaults to Redis db=3 so eviction policy doesn't
    clobber queue (db=0/2) or rate-limit (db=1) keysets."""
    frag = FRAGMENT_REGISTRY["cache_redis"]
    for lang in (BackendLanguage.PYTHON, BackendLanguage.NODE, BackendLanguage.RUST):
        env = dict(frag.implementations[lang].env_vars)
        assert "CACHE_REDIS_URL" in env
        # Dedicated DB; bare ``/`` is fine but ``/3`` is the canonical
        # default — checking the suffix keeps the test future-proof if
        # someone moves to a separate host.
        assert env["CACHE_REDIS_URL"].endswith("/3")


def test_cache_memory_env_var_max_entries() -> None:
    """Tunable cap surfaces via env so generated services can override
    without a code change."""
    frag = FRAGMENT_REGISTRY["cache_memory"]
    for lang in (BackendLanguage.PYTHON, BackendLanguage.NODE, BackendLanguage.RUST):
        env = dict(frag.implementations[lang].env_vars)
        assert "CACHE_MEMORY_MAX_ENTRIES" in env


# -- on-disk file shape -------------------------------------------------------


def _port_root(lang: BackendLanguage) -> Path:
    return Path(FRAGMENT_REGISTRY["cache_port"].implementations[lang].fragment_dir)


def _memory_root(lang: BackendLanguage) -> Path:
    return Path(FRAGMENT_REGISTRY["cache_memory"].implementations[lang].fragment_dir)


def _redis_root(lang: BackendLanguage) -> Path:
    return Path(FRAGMENT_REGISTRY["cache_redis"].implementations[lang].fragment_dir)


def test_python_port_and_adapter_files_land_at_conventional_paths() -> None:
    assert (
        _port_root(BackendLanguage.PYTHON) / "files" / "src" / "app" / "ports" / "cache.py"
    ).is_file()
    assert (
        _memory_root(BackendLanguage.PYTHON)
        / "files"
        / "src"
        / "app"
        / "adapters"
        / "cache"
        / "memory.py"
    ).is_file()
    assert (
        _redis_root(BackendLanguage.PYTHON)
        / "files"
        / "src"
        / "app"
        / "adapters"
        / "cache"
        / "redis.py"
    ).is_file()


def test_node_port_and_adapter_files_land_at_conventional_paths() -> None:
    assert (
        _port_root(BackendLanguage.NODE) / "files" / "src" / "app" / "ports" / "cache.ts"
    ).is_file()
    assert (
        _memory_root(BackendLanguage.NODE)
        / "files"
        / "src"
        / "app"
        / "adapters"
        / "cache"
        / "memory.ts"
    ).is_file()
    assert (
        _redis_root(BackendLanguage.NODE)
        / "files"
        / "src"
        / "app"
        / "adapters"
        / "cache"
        / "redis.ts"
    ).is_file()


def test_rust_port_and_adapter_files_land_at_conventional_paths() -> None:
    port_root = _port_root(BackendLanguage.RUST)
    assert (port_root / "files" / "src" / "ports" / "cache.rs").is_file()
    # mod.rs is now in the base template (shared via inject.yaml marker),
    # NOT shipped per-fragment.
    assert not (port_root / "files" / "src" / "ports" / "mod.rs").is_file()

    mem_root = _memory_root(BackendLanguage.RUST)
    assert (mem_root / "files" / "src" / "adapters" / "cache_memory.rs").is_file()
    assert not (mem_root / "files" / "src" / "adapters" / "mod.rs").is_file()

    redis_root = _redis_root(BackendLanguage.RUST)
    assert (redis_root / "files" / "src" / "adapters" / "cache_redis.rs").is_file()
    assert not (redis_root / "files" / "src" / "adapters" / "mod.rs").is_file()


# -- inject.yaml well-formedness ---------------------------------------------


def _load_inject(root: Path) -> list[dict]:
    entries = yaml.safe_load((root / "inject.yaml").read_text(encoding="utf-8"))
    assert isinstance(entries, list) and len(entries) >= 1
    return entries


def test_python_port_inject_targets_container() -> None:
    entries = _load_inject(_port_root(BackendLanguage.PYTHON))
    e = entries[0]
    assert e["target"] == "src/app/core/container.py"
    assert "APP_POST_CONFIGURE" in e["marker"]
    assert "CachePort" in e["snippet"]


def test_node_port_inject_targets_app_ts() -> None:
    entries = _load_inject(_port_root(BackendLanguage.NODE))
    e = entries[0]
    assert e["target"] == "src/app.ts"
    assert "MIDDLEWARE_IMPORTS" in e["marker"]
    assert "CachePort" in e["snippet"]


def test_rust_port_inject_registers_cache_submodule() -> None:
    entries = _load_inject(_port_root(BackendLanguage.RUST))
    e = entries[0]
    assert e["target"] == "src/ports/mod.rs"
    assert "PORTS_MOD_REGISTRATION" in e["marker"]
    assert "pub mod cache" in e["snippet"]


def test_memory_adapter_inject_wires_adapter_per_language() -> None:
    # Python — adapter imported into container.
    py = _load_inject(_memory_root(BackendLanguage.PYTHON))
    py_snippets = " ".join(e.get("snippet", "") for e in py)
    assert "MemoryCacheAdapter" in py_snippets
    # Node — adapter constructed inside app.ts.
    node = _load_inject(_memory_root(BackendLanguage.NODE))
    node_snippets = " ".join(e.get("snippet", "") for e in node)
    assert "MemoryCacheAdapter" in node_snippets
    # Rust — adapter submodule registered in adapters/mod.rs via marker.
    rust = _load_inject(_memory_root(BackendLanguage.RUST))
    rust_snippets = " ".join(e.get("snippet", "") for e in rust)
    assert "pub mod cache_memory" in rust_snippets


def test_redis_adapter_inject_wires_adapter_per_language() -> None:
    py = _load_inject(_redis_root(BackendLanguage.PYTHON))
    py_snippets = " ".join(e.get("snippet", "") for e in py)
    assert "RedisCacheAdapter" in py_snippets

    node = _load_inject(_redis_root(BackendLanguage.NODE))
    node_snippets = " ".join(e.get("snippet", "") for e in node)
    assert "RedisCacheAdapter" in node_snippets

    # Rust — adapter submodule registered in adapters/mod.rs via marker.
    rust = _load_inject(_redis_root(BackendLanguage.RUST))
    rust_snippets = " ".join(e.get("snippet", "") for e in rust)
    assert "pub mod cache_redis" in rust_snippets


# -- port + adapter source shape ---------------------------------------------


def test_python_port_declares_three_canonical_operations() -> None:
    body = (
        _port_root(BackendLanguage.PYTHON) / "files" / "src" / "app" / "ports" / "cache.py"
    ).read_text(encoding="utf-8")
    assert "class CachePort" in body
    for op in ("def get(", "def set(", "def invalidate("):
        assert op in body, f"python port missing operation: {op!r}"


def test_node_port_declares_three_canonical_operations() -> None:
    body = (
        _port_root(BackendLanguage.NODE) / "files" / "src" / "app" / "ports" / "cache.ts"
    ).read_text(encoding="utf-8")
    assert "interface CachePort" in body
    for op in ("get<", "set<", "invalidate("):
        assert op in body, f"node port missing operation: {op!r}"


def test_rust_port_declares_three_canonical_operations_as_trait() -> None:
    body = (_port_root(BackendLanguage.RUST) / "files" / "src" / "ports" / "cache.rs").read_text(
        encoding="utf-8"
    )
    assert "trait CachePort" in body
    for op in ("fn get(", "fn set(", "fn invalidate("):
        assert op in body, f"rust port missing operation: {op!r}"


# -- resolver dispatch --------------------------------------------------------


def _project(
    langs: list[BackendLanguage], options: dict[str, object] | None = None
) -> ProjectConfig:
    backends = [
        BackendConfig(name=f"svc-{i}", project_name="P", language=lang, server_port=5000 + i)
        for i, lang in enumerate(langs)
    ]
    return ProjectConfig(
        project_name="P",
        backends=backends,
        frontend=None,
        options=options or {},
    )


def test_resolver_pulls_in_port_and_memory_adapter_on_all_three_backends() -> None:
    """``reliability.cache=memory`` is the default-friendly path —
    zero external infra, ships on every backend."""
    for lang in (BackendLanguage.PYTHON, BackendLanguage.NODE, BackendLanguage.RUST):
        plan = resolve(_project([lang], {"reliability.cache": "memory"}))
        names = [rf.fragment.name for rf in plan.ordered]
        assert "cache_port" in names, f"{lang.value}: cache_port missing"
        assert "cache_memory" in names, f"{lang.value}: cache_memory missing"
        # Adapter must order after the port — strict-mode file copy
        # would still succeed (separate files), but the import chain
        # in inject.yaml requires the port present first.
        assert names.index("cache_port") < names.index("cache_memory")
        # The memory adapter itself doesn't declare a Redis capability
        # — other default-on fragments (e.g. rate_limit on Python) may
        # still ask for Redis, so we assert on the fragment's own
        # capability declaration, not the plan's aggregate set.
        assert "redis" not in FRAGMENT_REGISTRY["cache_memory"].capabilities


def test_resolver_pulls_in_port_and_redis_adapter_on_all_three_backends() -> None:
    for lang in (BackendLanguage.PYTHON, BackendLanguage.NODE, BackendLanguage.RUST):
        plan = resolve(_project([lang], {"reliability.cache": "redis"}))
        names = [rf.fragment.name for rf in plan.ordered]
        assert "cache_port" in names, f"{lang.value}: cache_port missing"
        assert "cache_redis" in names, f"{lang.value}: cache_redis missing"
        assert "cache_memory" not in names
        # Redis capability surfaces so docker_manager provisions the
        # sidecar — matches the queue_redis / rate_limit_redis pattern.
        assert "redis" in plan.capabilities


def test_resolver_none_strips_all_cache_code() -> None:
    """The ``none`` value MUST leave no cache-related fragments in the
    plan — that's the whole point of the strip path."""
    for lang in (BackendLanguage.PYTHON, BackendLanguage.NODE, BackendLanguage.RUST):
        plan = resolve(_project([lang], {"reliability.cache": "none"}))
        names = {rf.fragment.name for rf in plan.ordered}
        assert "cache_port" not in names
        assert "cache_memory" not in names
        assert "cache_redis" not in names


def test_resolver_default_value_strips_cache_code() -> None:
    """``reliability.cache`` defaults to ``none`` — users who don't
    touch the option get zero cache infrastructure."""
    plan = resolve(_project([BackendLanguage.PYTHON]))
    names = {rf.fragment.name for rf in plan.ordered}
    assert "cache_port" not in names
    assert "cache_memory" not in names
    assert "cache_redis" not in names


def test_resolver_redis_in_mixed_project_targets_every_backend() -> None:
    """Cache_redis is tier-1: all three backends get the adapter when
    selected, sharing one Redis sidecar in the compose file."""
    plan = resolve(
        _project(
            [BackendLanguage.PYTHON, BackendLanguage.NODE, BackendLanguage.RUST],
            {"reliability.cache": "redis"},
        )
    )
    by_name = {rf.fragment.name: rf for rf in plan.ordered}
    port_targets = by_name["cache_port"].target_backends
    adapter_targets = by_name["cache_redis"].target_backends
    for lang in (BackendLanguage.PYTHON, BackendLanguage.NODE, BackendLanguage.RUST):
        assert lang in port_targets, f"port missing target {lang.value}"
        assert lang in adapter_targets, f"redis adapter missing target {lang.value}"


# -- cache + queue coexistence on Rust ----------------------------------------


def test_cache_and_queue_port_coexist_rust() -> None:
    """cache_port + queue_port must resolve together on Rust without
    conflicts_with blocking the combination. Both ports inject into
    the shared ``ports/mod.rs`` via the ``FORGE:PORTS_MOD_REGISTRATION``
    marker with distinct sentinel blocks."""
    config = _project(
        [BackendLanguage.RUST],
        {"reliability.cache": "memory", "queue.backend": "apalis"},
    )
    plan = resolve(config)
    names = {rf.fragment.name for rf in plan.ordered}
    assert "cache_port" in names
    assert "queue_port" in names


def test_cache_memory_and_queue_apalis_coexist_rust() -> None:
    """Adapters inject into the shared ``adapters/mod.rs`` via the
    ``FORGE:ADAPTERS_MOD_REGISTRATION`` marker -- no file collision."""
    config = _project(
        [BackendLanguage.RUST],
        {"reliability.cache": "memory", "queue.backend": "apalis"},
    )
    plan = resolve(config)
    names = {rf.fragment.name for rf in plan.ordered}
    assert "cache_memory" in names
    assert "queue_apalis" in names


def test_cache_redis_and_queue_apalis_coexist_rust() -> None:
    """Both the redis cache adapter and the apalis queue adapter coexist
    on Rust -- no file or injection collision."""
    config = _project(
        [BackendLanguage.RUST],
        {"reliability.cache": "redis", "queue.backend": "apalis"},
    )
    plan = resolve(config)
    names = {rf.fragment.name for rf in plan.ordered}
    assert "cache_redis" in names
    assert "queue_apalis" in names


# -- distinctness from response_cache (HTTP middleware) -----------------------


def test_cache_port_is_distinct_from_response_cache_fragment() -> None:
    """``response_cache`` is HTTP-shape middleware (fastapi-cache2);
    ``cache_port`` is the generic K/V surface. Both exist; they must
    not be merged or aliased."""
    assert "response_cache" in FRAGMENT_REGISTRY, "response_cache must still exist"
    assert "cache_port" in FRAGMENT_REGISTRY
    # They MUST NOT share template directories.
    rc_py = Path(
        FRAGMENT_REGISTRY["response_cache"].implementations[BackendLanguage.PYTHON].fragment_dir
    )
    cp_py = Path(
        FRAGMENT_REGISTRY["cache_port"].implementations[BackendLanguage.PYTHON].fragment_dir
    )
    assert rc_py != cp_py


def test_cache_port_and_response_cache_can_co_exist_in_plan() -> None:
    """A project can use the response-cache HTTP middleware AND the
    generic cache port — they're orthogonal."""
    plan = resolve(
        _project(
            [BackendLanguage.PYTHON],
            {
                "middleware.response_cache": True,
                "reliability.cache": "memory",
            },
        )
    )
    names = {rf.fragment.name for rf in plan.ordered}
    assert "response_cache" in names
    assert "cache_port" in names
    assert "cache_memory" in names


# -- in-memory adapter behaviour (unit test of the Python adapter source) -----


def _load_python_memory_adapter():
    """Dynamically import the in-process MemoryCacheAdapter template
    file so we can exercise its TTL behaviour. The adapter imports
    ``app.ports.cache``; we stub a tiny ``CachePort`` Protocol module
    so the import resolves without a generated project context.
    """
    # Stub ``app.ports.cache`` — the adapter only needs the symbol to
    # exist; the runtime ``Protocol`` check is structural so any class
    # with the right methods satisfies it.
    #
    # Backfill each module *individually* rather than gating all three on
    # ``"app" not in sys.modules``: under ``pytest -n auto`` a sibling test
    # on the same worker may have already inserted a bare ``app`` package
    # (without ``app.ports``), and a skip-if-present guard would then leave
    # ``app.ports`` unstubbed → ``ModuleNotFoundError`` at import.
    import types

    if "app" not in sys.modules:
        app_mod = types.ModuleType("app")
        app_mod.__path__ = []  # mark as package
        sys.modules["app"] = app_mod
    if "app.ports" not in sys.modules:
        ports_mod = types.ModuleType("app.ports")
        ports_mod.__path__ = []
        sys.modules["app.ports"] = ports_mod
    if "app.ports.cache" not in sys.modules:
        cache_proto_mod = types.ModuleType("app.ports.cache")

        class _StubCachePort:  # noqa: D401 — stub for Protocol surface
            pass

        cache_proto_mod.CachePort = _StubCachePort
        sys.modules["app.ports.cache"] = cache_proto_mod

    src = (
        _memory_root(BackendLanguage.PYTHON)
        / "files"
        / "src"
        / "app"
        / "adapters"
        / "cache"
        / "memory.py"
    )
    spec = importlib.util.spec_from_file_location("_forge_cache_memory_under_test", src)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.MemoryCacheAdapter


def test_memory_adapter_get_set_invalidate_roundtrip() -> None:
    AdapterCls = _load_python_memory_adapter()
    adapter = AdapterCls(max_entries=8)

    async def _run() -> None:
        await adapter.set("k", {"v": 1})
        got = await adapter.get("k")
        assert got == {"v": 1}
        await adapter.invalidate("k")
        assert await adapter.get("k") is None

    asyncio.run(_run())


def test_memory_adapter_ttl_eviction() -> None:
    """0.1s TTL — set, sleep past expiry, observe miss. This is the
    behavioural guarantee idempotency-key callers rely on; a missing
    expiry would re-serve stale tokens forever."""
    AdapterCls = _load_python_memory_adapter()
    adapter = AdapterCls(max_entries=8)

    async def _run() -> None:
        await adapter.set("k", "v", ttl_seconds=1)
        # Before expiry — still there.
        assert await adapter.get("k") == "v"
        # Subsecond TTL via internal monotonic clock — bypass the
        # public API by writing a custom short deadline. Sleep + retry
        # is the most readable assertion shape; we keep TTL=1s but use
        # a manual clock fast-forward by re-setting with ttl_seconds=0
        # (the documented "write-but-immediately-expired" no-op).
        await adapter.set("k", "v", ttl_seconds=0)
        assert await adapter.get("k") is None

        # Real-time TTL path: set a 1-tick TTL and sleep. Keeps the
        # test fast (~110ms) while exercising the expiry-on-read code
        # path the prior assertion stubs out.
        # Manually poke the internal state to give us a sub-second
        # deadline; production callers stick to the integer-second API.
        import time

        adapter._store["short"] = ("v2", time.monotonic() + 0.05)  # noqa: SLF001
        await asyncio.sleep(0.12)
        assert await adapter.get("short") is None

    asyncio.run(_run())


def test_memory_adapter_lru_eviction_under_capacity_pressure() -> None:
    """The cap is enforced — older entries fall out so the cache
    doesn't grow unbounded."""
    AdapterCls = _load_python_memory_adapter()
    adapter = AdapterCls(max_entries=3)

    async def _run() -> None:
        await adapter.set("a", 1)
        await adapter.set("b", 2)
        await adapter.set("c", 3)
        # All three present.
        assert await adapter.get("a") == 1
        # Insert a fourth — oldest (which is now "b" because the get
        # above moved "a" to the most-recent end) gets evicted.
        await adapter.set("d", 4)
        assert await adapter.get("b") is None
        assert await adapter.get("a") == 1
        assert await adapter.get("c") == 3
        assert await adapter.get("d") == 4

    asyncio.run(_run())


# -- guard against accidental option-name collisions -------------------------


def test_reliability_cache_option_is_independent_from_middleware_response_cache() -> None:
    """``reliability.cache`` and ``middleware.response_cache`` are
    distinct options. Touching one MUST NOT toggle the other."""
    from forge.options import OPTION_REGISTRY

    assert "reliability.cache" in OPTION_REGISTRY
    assert "middleware.response_cache" in OPTION_REGISTRY
    cache_enables = OPTION_REGISTRY["reliability.cache"].enables
    response_enables = OPTION_REGISTRY["middleware.response_cache"].enables
    # The two enable maps share zero fragments.
    cache_fragments = set().union(*cache_enables.values())
    response_fragments = set().union(*response_enables.values())
    assert cache_fragments.isdisjoint(response_fragments)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("memory", {"cache_port", "cache_memory"}),
        ("redis", {"cache_port", "cache_redis"}),
        ("none", set()),
    ],
)
def test_reliability_cache_enables_table(value: str, expected: set[str]) -> None:
    from forge.options import OPTION_REGISTRY

    opt = OPTION_REGISTRY["reliability.cache"]
    assert set(opt.enables.get(value, ())) == expected
