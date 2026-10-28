"""Compile a ``ProjectConfig.options`` mapping into an ordered plan of
template fragments.

The resolver does four things:
    1. Apply Option defaults for every path the user didn't set.
    2. Translate each (path, value) into the fragment set via
       ``Option.enables[value]``.
    3. Topologically sort the fragments by ``Fragment.depends_on``.
    4. Reject conflicting fragments (two vector stores declared
       directly, for example — rare because Options usually ensure
       mutual exclusion by construction).

Output is a ``ResolvedPlan`` that downstream code (``generator``,
``feature_injector``, ``docker_manager``) already knows how to consume.
The plan's ``ordered`` sequence is stable across runs for a given config
so generation + re-application produce identical output.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from forge.config import BackendLanguage, ProjectConfig
from forge.errors import (
    OPTIONS_DEP_CYCLE,
    OPTIONS_FRAGMENT_CONFLICT,
    OPTIONS_INVALID_VALUE,
    OPTIONS_MISSING_FRAGMENT,
    OPTIONS_UNKNOWN_PATH,
    OptionsError,
)
from forge.fragments import FRAGMENT_REGISTRY, Fragment
from forge.options import OPTION_REGISTRY, resolve_alias

if TYPE_CHECKING:
    from forge.components import ComponentNode

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolvedFragment:
    """A single fragment, resolved to a concrete set of target backends."""

    fragment: Fragment
    # Backends (in project order) on which this fragment will be applied.
    target_backends: tuple[BackendLanguage, ...]


@dataclass(frozen=True)
class ResolvedPlan:
    """The compiled output of ``resolve()``.

    ``ordered`` is the topologically-sorted list of fragments to apply.
    ``capabilities`` is the union of ``Fragment.capabilities`` across
    the plan — ``docker_manager.render_compose`` uses it to decide
    which extra services to provision (redis, qdrant, etc.).
    ``option_values`` is the fully-defaulted mapping of option path →
    value, useful for template context variables (e.g. rag.top_k).
    """

    ordered: tuple[ResolvedFragment, ...]
    capabilities: frozenset[str]
    option_values: dict[str, object]


# -----------------------------------------------------------------------------
# Back-compat alias: many downstream modules still iterate `plan.ordered`
# and access `rf.spec` / `rf.config` / `rf.target_backends`. Provide a
# shim so the feature_injector and generator keep working during the
# migration.
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedFeature:
    """Legacy shim. Mirrors the ResolvedFragment shape with the field names
    downstream code used to expect.
    """

    spec: Fragment  # historically FeatureSpec; Fragment has the fields the
    # injector actually reads (key→name, implementations, depends_on)
    target_backends: tuple[BackendLanguage, ...]

    @property
    def config(self) -> dict[str, object]:
        """Empty placeholder — previously carried FeatureConfig.options.
        Nothing in the codebase reads it post-refactor, but keeping the
        property lets existing test fixtures parse without attribute
        errors."""
        return {}


# -----------------------------------------------------------------------------
# Core resolve
# -----------------------------------------------------------------------------


def _apply_option_defaults(user_options: dict[str, object]) -> dict[str, object]:
    """Return a fully-defaulted option mapping.

    Every registered Option appears in the output; user values override
    defaults. Unknown keys in ``user_options`` raise — ProjectConfig's
    validator should have caught them earlier, but failing loudly here
    too keeps the pipeline safe.

    Epic G (1.1.0-alpha.1) — alias rewrites. If a user-supplied path is
    a declared alias on some Option, the value transparently maps to the
    canonical path and a deprecation warning is logged. The
    ``forge migrate-rename-options`` codemod rewrites the user's
    forge.toml so the warning stops firing.
    """
    rewritten: dict[str, object] = {}
    for path, value in user_options.items():
        canonical = resolve_alias(path)
        if canonical is not None:
            if canonical in rewritten or canonical in user_options:
                # User set both the alias and the canonical path. The
                # canonical wins — silently dropping the alias would mask
                # the shadowing, but promoting the alias would shadow the
                # user's explicit canonical value. Raise so the user
                # notices + picks one.
                raise OptionsError(
                    f"Option path {path!r} is a deprecated alias for "
                    f"{canonical!r}, but {canonical!r} is also set. "
                    f"Keep only one.",
                    code=OPTIONS_UNKNOWN_PATH,
                    context={"alias": path, "canonical": canonical},
                )
            logger.warning(
                "Option path %r is a deprecated alias for %r. Run "
                "`forge migrate-rename-options` to rewrite forge.toml.",
                path,
                canonical,
            )
            rewritten[canonical] = value
        elif path in OPTION_REGISTRY:
            rewritten[path] = value
        else:
            known = ", ".join(sorted(OPTION_REGISTRY)) or "(none registered)"
            raise OptionsError(
                f"Unknown option '{path}'. Known options: {known}",
                code=OPTIONS_UNKNOWN_PATH,
                context={"path": path},
            )

    resolved: dict[str, object] = {}
    for path, opt in OPTION_REGISTRY.items():
        resolved[path] = rewritten.get(path, opt.default)
    return resolved


def _collect_fragments(option_values: dict[str, object]) -> set[str]:
    """Compile options' ``enables`` maps into a flat set of fragment names."""
    fragments: set[str] = set()
    for path, value in option_values.items():
        spec = OPTION_REGISTRY[path]
        # LIST / STR / INT / OBJECT options never map values to fragments —
        # validation in `_validate_enables_shape` already forbids it. Skip
        # the dict lookup for two reasons: (1) LIST values are unhashable
        # and would raise here, and (2) the lookup is pointless when the
        # map is empty.
        if not spec.enables:
            continue
        fragments.update(spec.enables.get(value, ()))
    return fragments


def _expand_deps(fragment_set: set[str]) -> set[str]:
    """Pull every transitive dep into the fragment set.

    ``Fragment.depends_on`` is a tuple of fragment names. If a fragment
    is in the plan, so must its deps be — users never see these, so
    we auto-include transparently (no error).
    """
    added = True
    while added:
        added = False
        for name in list(fragment_set):
            spec = FRAGMENT_REGISTRY.get(name)
            if spec is None:
                raise OptionsError(
                    f"Option references unknown fragment '{name}'. Registry "
                    "out of sync — did you rename a fragment directory without "
                    "updating options.py?",
                    code=OPTIONS_MISSING_FRAGMENT,
                    context={"fragment": name},
                )
            for dep in spec.depends_on:
                if dep not in fragment_set:
                    fragment_set.add(dep)
                    added = True
    return fragment_set


def _build_dep_graph(fragment_names: set[str]) -> dict[str, set[str]]:
    """Combined depends_on + before/after graph for the in-plan fragments.

    Returns a mapping ``name -> {names this fragment must apply after}``.
    ``depends_on`` edges are unconditional (the resolver already pulled
    transitive deps into ``fragment_names`` via ``_expand_deps``).
    ``before`` / ``after`` edges are SOFT — they only constrain the sort
    when both endpoints are already in the plan; otherwise they're inert.
    """
    graph: dict[str, set[str]] = {n: set() for n in fragment_names}
    for n in fragment_names:
        frag = FRAGMENT_REGISTRY[n]
        for dep in frag.depends_on:
            if dep in fragment_names:
                graph[n].add(dep)
        for target in frag.after:
            if target in fragment_names:
                graph[n].add(target)
        for target in frag.before:
            if target in fragment_names:
                graph.setdefault(target, set()).add(n)
    return graph


def _topo_sort(fragment_names: set[str]) -> list[str]:
    """Kahn's algorithm over the combined depends_on + before/after graph.

    Sorts ``ready`` sets by (Fragment.order, name) so middleware
    layering is deterministic across runs.
    """
    graph = _build_dep_graph(fragment_names)
    remaining = set(fragment_names)
    order: list[str] = []
    order_set: set[str] = set()

    while remaining:
        ready = [name for name in remaining if graph[name].issubset(order_set)]
        if not ready:
            cyclic = ", ".join(sorted(remaining))
            raise OptionsError(
                f"Cyclic fragment dependency detected among: {cyclic}. "
                "Inspect `depends_on` / `before` / `after` entries in fragments.py.",
                code=OPTIONS_DEP_CYCLE,
                context={"fragments": sorted(remaining)},
            )
        ready.sort(key=lambda n: (FRAGMENT_REGISTRY[n].order, n))
        order.extend(ready)
        order_set.update(ready)
        remaining.difference_update(ready)
    return order


def _check_conflicts(
    fragment_names: set[str], project_backends: tuple[BackendLanguage, ...] = ()
) -> None:
    present = set(project_backends)
    for name in fragment_names:
        spec = FRAGMENT_REGISTRY[name]
        for other in spec.conflicts_with:
            if other not in fragment_names:
                continue
            # Backend-scoped conflicts (e.g. the Rust-only shared
            # ``src/ports/mod.rs`` collision between llm/queue/cache ports)
            # only fire when BOTH fragments land on a project backend of a
            # scoped language. An unscoped conflict (the default) fires
            # unconditionally, preserving prior behavior.
            if spec.conflict_backends:
                peer = FRAGMENT_REGISTRY[other]
                shared = (
                    set(_target_backends(spec, project_backends))
                    & set(_target_backends(peer, project_backends))
                    & set(spec.conflict_backends)
                )
                # ``present`` is empty for callers that don't pass backends
                # (legacy / pure fragment-set checks) — fall back to the
                # unconditional behavior so we never silently skip a conflict.
                if present and not shared:
                    continue
            a, b = sorted([name, other])
            raise OptionsError(
                f"Fragments '{a}' and '{b}' conflict and cannot both be enabled.",
                code=OPTIONS_FRAGMENT_CONFLICT,
                context={"fragments": [a, b]},
            )


def _target_backends(
    frag: Fragment, project_backends: tuple[BackendLanguage, ...]
) -> tuple[BackendLanguage, ...]:
    """Backends in the project that this fragment supports; project order."""
    return tuple(lang for lang in project_backends if frag.supports(lang))


def _validate_reads_options(fragment_names: set[str]) -> None:
    """Epic E — assert every ``impl.reads_options`` path is a known Option.

    A typo in ``reads_options`` would surface today as a silent missing-key
    in the filtered ``FragmentContext.options`` at apply time; the
    fragment would run with less context than it declared. Surfacing the
    orphan at resolve time keeps the error next to the declaration.
    """
    for name in fragment_names:
        frag = FRAGMENT_REGISTRY[name]
        for impl in frag.implementations.values():
            for path in impl.reads_options:
                if path not in OPTION_REGISTRY:
                    raise OptionsError(
                        f"Fragment {name!r} reads_options includes unknown "
                        f"option path {path!r}. Registered paths: "
                        f"{', '.join(sorted(OPTION_REGISTRY)) or '(none)'}",
                        code=OPTIONS_UNKNOWN_PATH,
                        context={"fragment": name, "path": path},
                    )


# Option values whose only real implementation is on a subset of backend
# languages. A user-selected value with no compatible project backend
# hard-errors at config time instead of silently emitting the abstract port
# with no adapter (a service that starts, then fails at the first call).
# ``openai`` is intentionally absent: its TS (``@ai-sdk/openai``), Rust
# (``async-openai``) and Go (``openai-go``) adapters are real, so it stays
# valid on every backend.
_VALUE_REQUIRES_BACKEND: dict[tuple[str, object], frozenset[BackendLanguage]] = {
    ("llm.provider", "anthropic"): frozenset({BackendLanguage.PYTHON}),
    ("llm.provider", "ollama"): frozenset({BackendLanguage.PYTHON}),
    ("llm.provider", "bedrock"): frozenset({BackendLanguage.PYTHON}),
    # Each queue backend ships its ADAPTER on exactly one language (the
    # ``queue_port`` interface is tri-language, but a port with no adapter is
    # a service that boots then fails at first use). Reject the mismatch at
    # config time instead of silently emitting the adapter-less port.
    ("queue.backend", "redis"): frozenset({BackendLanguage.PYTHON}),
    ("queue.backend", "sqs"): frozenset({BackendLanguage.PYTHON}),
    ("queue.backend", "bullmq"): frozenset({BackendLanguage.NODE}),
    ("queue.backend", "apalis"): frozenset({BackendLanguage.RUST}),
    ("queue.backend", "asynq"): frozenset({BackendLanguage.GO}),
    # ``shared_rls`` ships on Python and Go (``allowed_backends`` on the
    # option); the per-tenant-schema strategy is Python-only.
    ("database.multitenancy", "schema_per_tenant"): frozenset({BackendLanguage.PYTHON}),
}

# Options where EVERY "active" (non-none / non-false) value is Python-only in
# 1.x — the whole RAG vector-store stack and the MCP server fragments ship
# Python implementations only, so selecting them on a Node/Rust-only project
# yields a service with zero of the requested capability. Checked the same way
# as ``_VALUE_REQUIRES_BACKEND`` but without enumerating every provider value.
_PYTHON_ONLY_WHEN_ACTIVE: dict[str, frozenset[BackendLanguage]] = {
    "rag.backend": frozenset({BackendLanguage.PYTHON}),
    "platform.mcp": frozenset({BackendLanguage.PYTHON}),
    # agent.mode (llm_only/tool_calling/multi_agent) fans out almost entirely to
    # Python-only fragments (agent, agent_streaming, agent_tools,
    # conversation_persistence, mcp_server, mcp_ui) plus the multi-language
    # llm_port. On a Node/Rust/Go-only project it would silently resolve to just
    # llm_port — the requested agent capability missing, and a frontend chat
    # base-url pointing at an endpoint never generated. 'none' is inactive via
    # _INACTIVE_VALUES. (audit #16)
    "agent.mode": frozenset({BackendLanguage.PYTHON}),
    # object_store ships only Python port + adapters today; selecting it on a
    # Node/Rust-only project would otherwise resolve to ZERO fragments (a
    # complete silent no-op).
    "object_store.backend": frozenset({BackendLanguage.PYTHON}),
}

# Values that mean "feature off" for the options in _PYTHON_ONLY_WHEN_ACTIVE.
_INACTIVE_VALUES: frozenset[object] = frozenset({None, "", "none", False})


def _check_option_allowed_backends(
    config: ProjectConfig, project_backends: tuple[BackendLanguage, ...]
) -> None:
    """Enforce each Option's ``allowed_backends`` metadata at resolve time.

    ``Option.allowed_backends`` declares the built-in backend languages an
    option's ACTIVE values support (``None`` = any). The metadata has long
    existed on the Option dataclass and is checked by
    ``ProjectConfig.validate()`` (``_check_option_allowed_backends``), but the
    resolver — the path the matrix runner / headless ``generate()`` /
    in-memory callers take without going through ``ProjectConfig.validate``'s
    full layer-target walker — did not enforce it. Wire it here so a
    non-``none`` ``database.multitenancy`` strategy on a Node/Rust project is
    rejected with a clear ``OptionsError`` rather than silently dropping the
    Python-only fragment.

    Only enforced for:

    - options the user EXPLICITLY set (origin ``"user"``) — a persisted
      default must never hard-error;
    - ACTIVE values (``Option.is_active_value`` — an ENUM value whose
      ``enables`` is non-empty / a ``True`` BOOL). ``none``-style values map
      to no fragments and are valid on every backend, so the default never
      trips the check.

    A project with zero backends (``backend.mode=none``) is skipped — there is
    nothing to constrain, and ``requires_backend`` handles that case.
    """
    if not project_backends:
        return
    origins = config.option_origins or {}
    present = set(project_backends)
    effective = _apply_option_defaults(config.options)
    # Canonical paths the user explicitly supplied (alias-resolved).
    user_set_paths = {resolve_alias(p) or p for p in config.options}
    for path, opt in OPTION_REGISTRY.items():
        if opt.allowed_backends is None:
            continue
        # Only constrain paths the user actually selected — never a default.
        if path not in user_set_paths:
            continue
        if origins.get(path, "user") != "user":
            continue
        value = effective.get(path, opt.default)
        if not opt.is_active_value(value):
            continue
        if not present.isdisjoint(opt.allowed_backends):
            continue
        allowed = ", ".join(sorted(b.value for b in opt.allowed_backends))
        have = ", ".join(b.value for b in project_backends) or "(none)"
        raise OptionsError(
            f"Option '{path}={value!r}' only supports backend language(s) "
            f"{allowed}, but this project's backends are {have}. "
            f"Choose a value supported on your backend, or add a {allowed} backend.",
            code=OPTIONS_INVALID_VALUE,
            context={
                "option": path,
                "value": value,
                "allowed_backends": sorted(b.value for b in opt.allowed_backends),
                "project_backends": [b.value for b in project_backends],
            },
        )


def _check_app_template_exclusions(config: ProjectConfig) -> None:
    """Reject a USER-selected option whose every enabled fragment is excluded
    by every project backend's ``app_template`` variant.

    ``Fragment.excluded_app_templates`` makes default-on fragments silently
    skip incompatible variants (e.g. the HTTP middleware set on the
    ``worker`` variant, which ships no FastAPI app — see the merge driver's
    per-backend opt-out). Silent skipping is right for defaults but wrong
    for explicit selections: a user who asks a worker-only project for
    ``middleware.rate_limit=true`` would otherwise get nothing, with exit 0.
    Hard-error at resolve time instead, mirroring the origin discipline of
    ``_check_option_allowed_backends``.
    """
    if not config.backends:
        return
    origins = config.option_origins or {}
    effective = _apply_option_defaults(config.options)
    user_set_paths = {resolve_alias(p) or p for p in config.options}
    for path, opt in OPTION_REGISTRY.items():
        if path not in user_set_paths:
            continue
        if origins.get(path, "user") != "user":
            continue
        value = effective.get(path, opt.default)
        if not opt.is_active_value(value):
            continue
        frag_names = tuple(opt.enables.get(value, ()))
        if not frag_names:
            continue
        applies_somewhere = False
        excluding_variants: set[str] = set()
        for name in frag_names:
            frag = FRAGMENT_REGISTRY.get(name)
            if frag is None:
                continue
            for bc in config.backends:
                impl = frag.implementations.get(bc.language)
                if impl is None:
                    # Language coverage gaps are _check_value_backend_support's
                    # job; this check only owns variant exclusions.
                    continue
                if impl.scope != "backend":
                    applies_somewhere = True  # project-scoped impls always land
                    break
                if (bc.app_template or "") in frag.excluded_app_templates:
                    excluding_variants.add(bc.app_template or "(unset)")
                    continue
                applies_somewhere = True
                break
            if applies_somewhere:
                break
        if applies_somewhere or not excluding_variants:
            continue
        variants = ", ".join(sorted(excluding_variants))
        raise OptionsError(
            f"Option '{path}={value!r}' is incompatible with this project's "
            f"backend app_template variant(s) ({variants}): every fragment it "
            f"enables ({', '.join(frag_names)}) is excluded on that service "
            f"shape. Drop the option, or add a backend with a compatible "
            f"app_template (e.g. crud-service).",
            code=OPTIONS_INVALID_VALUE,
            context={
                "option": path,
                "value": value,
                "fragments": list(frag_names),
                "excluding_app_templates": sorted(excluding_variants),
            },
        )


def _check_value_backend_support(
    config: ProjectConfig, project_backends: tuple[BackendLanguage, ...]
) -> None:
    """Reject user-selected option values that no project backend supports.

    Implements the "fail at config time, not silently at runtime" policy for
    polyglot footguns (e.g. ``llm.provider=anthropic`` on a Node/Rust-only
    project). Only user-origin selections are checked — a persisted default
    must never hard-error. See ``_VALUE_REQUIRES_BACKEND``.
    """
    origins = config.option_origins or {}
    present = set(project_backends)
    for path, value in config.options.items():
        if origins.get(path, "user") != "user":
            continue
        try:
            required = _VALUE_REQUIRES_BACKEND.get((path, value))
        except TypeError:
            # Unhashable value (list/dict option) — never constrained here.
            continue
        if required is None and path in _PYTHON_ONLY_WHEN_ACTIVE:
            try:
                active = value not in _INACTIVE_VALUES
            except TypeError:
                active = True
            if active:
                required = _PYTHON_ONLY_WHEN_ACTIVE[path]
        if required is None or not present.isdisjoint(required):
            continue
        req = ", ".join(sorted(b.value for b in required))
        have = ", ".join(b.value for b in project_backends) or "(none)"
        raise OptionsError(
            f"Option '{path}={value!r}' is only supported on backend "
            f"language(s) {req}, but this project's backends are {have}. "
            f"Choose a value supported on your backend, or add a {req} backend.",
            code=OPTIONS_INVALID_VALUE,
            context={
                "option": path,
                "value": value,
                "required_backends": sorted(b.value for b in required),
                "project_backends": [b.value for b in project_backends],
            },
        )


# Auth providers whose token authority depends on the keycloak + redis
# sidecars. Only these force the ``auth.mode``→``none`` coercion when
# ``include_keycloak`` is off (see ``resolve``). ``in_memory`` mints tokens in
# process and ``oidc_generic`` points at an external IdP, so neither needs the
# local keycloak stack and both stay ``auth.mode=generate``.
_KEYCLOAK_DEPENDENT_PROVIDERS: frozenset[str] = frozenset({"gatekeeper"})


def _provider_needs_keycloak(provider: object) -> bool:
    """True if ``auth.provider`` requires the local keycloak + redis stack.

    A non-string / unset provider conservatively counts as keycloak-dependent
    so the pre-provider-split coercion behaviour is preserved for any caller
    that hasn't threaded an ``auth.provider`` value (the default is
    ``gatekeeper``, which needs keycloak anyway).
    """
    if not isinstance(provider, str):
        return True
    return provider in _KEYCLOAK_DEPENDENT_PROVIDERS


# ``database.multitenancy`` strategies that are recognised values but ship no
# realisation yet. The option ACCEPTS them (so a forge.toml pinning one isn't
# rejected outright by value-validation), but the resolver turns a USER
# selection of either into an explicit "not yet implemented" error rather than
# silently generating an un-isolated project (the value maps to no fragments,
# which would otherwise be an invisible no-op).
_MULTITENANCY_DEFERRED: frozenset[str] = frozenset({"db_per_tenant"})


def _check_multitenancy_deferred(config: ProjectConfig) -> None:
    """Raise a clear error when a user selects a deferred multitenancy strategy.

    ``db_per_tenant`` is KNOWN (validation accepts it) but NOT implemented in
    1.x (``shared_rls`` and ``schema_per_tenant`` are). Only a user-origin
    selection errors — a persisted default never would (the default is
    ``none``). The message points at the implemented alternatives so the
    failure is actionable, not a dead end.
    """
    path = "database.multitenancy"
    value = config.options.get(path)
    if value not in _MULTITENANCY_DEFERRED:
        return
    origins = config.option_origins or {}
    if origins.get(path, "user") != "user":
        return
    raise OptionsError(
        f"database.multitenancy={value!r} is a recognised but NOT-yet-"
        f"implemented tenant-isolation strategy. forge accepts the value in a "
        f"forge.toml (so a future version can realise it without a config "
        f"migration), but generation cannot proceed: it would produce a "
        f"project with no tenant isolation. Use database.multitenancy="
        f"'shared_rls' (Postgres Row-Level Security; Python and Go) or "
        f"'schema_per_tenant' (per-tenant Postgres schema; Python) today — or "
        f"'none' for application-layer scoping only.",
        code=OPTIONS_INVALID_VALUE,
        context={
            "option": path,
            "value": value,
            "implemented": ["none", "shared_rls", "schema_per_tenant"],
        },
    )


# Tenant-isolation strategies each Go service implements. A Go service the
# selected strategy does not reach would run without tenant isolation, so a
# project with Go services must pick one Go implements.
_GO_MULTITENANCY: frozenset[str] = frozenset({"none", "shared_rls"})


def _check_go_multitenancy(
    config: ProjectConfig, project_backends: tuple[BackendLanguage, ...]
) -> None:
    """Reject a tenant-isolation strategy that would leave Go services unisolated."""
    path = "database.multitenancy"
    value = config.options.get(path, "none")
    if BackendLanguage.GO not in project_backends or value in _GO_MULTITENANCY:
        return
    raise OptionsError(
        f"database.multitenancy={value!r} has no Go implementation, so this "
        f"project's Go services would run without tenant isolation. Use "
        f"database.multitenancy='shared_rls' (Python and Go), or move the Go "
        f"services to a separate configuration.",
        code=OPTIONS_INVALID_VALUE,
        context={
            "option": path,
            "value": value,
            "go_implements": sorted(_GO_MULTITENANCY - {"none"}),
            "project_backends": [b.value for b in project_backends],
        },
    )


_DB_ISOLATING_MULTITENANCY: frozenset[str] = frozenset({"shared_rls", "schema_per_tenant"})
_CLIENT_SUPPLIED_TENANT: frozenset[str] = frozenset({"header", "subdomain"})


def _check_unverified_tenant(option_values: dict[str, object]) -> None:
    """Refuse a client-chosen tenant that no verified identity backs.

    Checked against the EFFECTIVE ``auth.mode`` (after the no-keycloak
    coercion), since that is what the generated services run with.
    ``database.allow_unverified_tenant`` opts out for a trusted gateway.
    """
    strategy = option_values.get("database.multitenancy")
    resolution = option_values.get("database.tenant_resolution")
    if (
        strategy not in _DB_ISOLATING_MULTITENANCY
        or resolution not in _CLIENT_SUPPLIED_TENANT
        or option_values.get("auth.mode") != "none"
        or option_values.get("database.allow_unverified_tenant")
    ):
        return
    raise OptionsError(
        f"database.multitenancy={strategy!r} with database.tenant_resolution="
        f"{resolution!r} and auth.mode=none lets any caller choose the tenant: "
        f"nothing verifies who sends the request, so anyone reads any tenant's "
        f"data. Set auth.mode=generate (with include_keycloak, or auth.provider="
        f"oidc_generic/in_memory), use database.tenant_resolution='token_claim', "
        f"or set database.allow_unverified_tenant=true when a trusted gateway "
        f"authenticates callers and owns the tenant header.",
        code=OPTIONS_INVALID_VALUE,
        context={
            "database.multitenancy": strategy,
            "database.tenant_resolution": resolution,
            "auth.mode": "none",
        },
    )


# The tenant source each self-registration mode gives new users a tenant from.
_SELF_REGISTRATION_SOURCE = {
    "own_organization": "organization",
    "default_tenant": "user_attribute",
}


def _check_self_registration(option_values: dict[str, object]) -> None:
    """A self-registration mode assigns a tenant of its own tenant source.

    ``own_organization`` creates an organization; ``default_tenant`` sets the
    ``tenant_id`` attribute.
    """
    source = option_values.get("auth.keycloak_tenant_source", "organization")
    mode = option_values.get("auth.keycloak_self_registration", "off")
    needed = _SELF_REGISTRATION_SOURCE.get(str(mode))
    if needed is None or source == needed:
        return
    raise OptionsError(
        f"auth.keycloak_self_registration={mode!r} gives new users a tenant through "
        f"auth.keycloak_tenant_source={needed!r}, but it is {source!r}. Set "
        f"auth.keycloak_tenant_source={needed!r}, or keep "
        "auth.keycloak_self_registration='off'.",
        code=OPTIONS_INVALID_VALUE,
        context={
            "auth.keycloak_self_registration": mode,
            "auth.keycloak_tenant_source": source,
        },
    )


def _check_security_constraints(option_values: dict[str, object], fragment_set: set[str]) -> None:
    """Cross-option safety rules enforced at config time.

    The generated MCP server exposes tool invocation + an audit log; shipping
    it with the auth stack disabled would leave those endpoints open behind no
    identity at all. Checked against the RESOLVED fragment set so it covers
    every path that pulls in ``mcp_server`` — both ``platform.mcp=true`` and
    ``agent.mode=tool_calling``. Checked against the EFFECTIVE ``auth.mode``
    (``option_values``, post-coercion) — not raw ``config.options`` — so the
    no-keycloak coercion (``auth.mode``→``none``) can't slip an unauthenticated
    MCP server past this guard.
    """
    if "mcp_server" in fragment_set and option_values.get("auth.mode") == "none":
        raise OptionsError(
            "Enabling the MCP server (platform.mcp=true or "
            "agent.mode=tool_calling) requires authentication, but "
            "auth.mode=none. The MCP server exposes tool invocation and an "
            "audit log; generating it without the auth stack would leave those "
            "endpoints open. Set auth.mode=generate, or disable MCP.",
            code=OPTIONS_INVALID_VALUE,
            context={"fragment": "mcp_server", "auth.mode": "none"},
        )


def _collect_component_fragments(config: ProjectConfig) -> set[str]:
    """Expand ``config.components`` into their emitter-fragment names.

    Additive + guarded: a project with no selected components returns the empty
    set, so the existing option/fragment flow is byte-identical. Component
    resolution (layering, versions, cycles, dependents) happens in
    ``forge.components.resolve_components`` and reuses the same error codes.
    """
    components = list(getattr(config, "components", None) or [])
    if not components:
        return set()
    # Local import avoids a module-load cycle (forge.components imports
    # forge.fragments/feature_manifest, which the resolver also touches).
    from forge.components import (  # noqa: PLC0415
        COMPONENT_REGISTRY,
        component_fragment_name,
        resolve_components,
    )

    resolved = resolve_components(components, COMPONENT_REGISTRY)
    _check_component_ui_kits(config, components, resolved.ordered, COMPONENT_REGISTRY)
    return {component_fragment_name(name) for name in resolved.ordered}


def _project_ui_kits(config: ProjectConfig) -> tuple[str, ...]:
    """The UI kits of the project's Vue frontends, primary first, without repeats.

    Empty when no frontend has kits. A single-frontend project has at most one.
    """
    from forge.ui_kits import applies_to  # noqa: PLC0415

    kits: list[str] = []
    for fc in config.active_frontends:
        if applies_to(fc.framework) and fc.ui_kit not in kits:
            kits.append(fc.ui_kit)
    return tuple(kits)


def _component_closure(name: str, registry: Mapping[str, ComponentNode]) -> set[str]:
    """``name`` and every component it pulls in, transitively."""
    closure = {name}
    found = [name]
    # ``found`` grows while it is walked, so each component is visited once.
    for current in found:
        node = registry.get(current)
        for child in node.children if node is not None else ():
            if child not in closure:
                closure.add(child)
                found.append(child)
    return closure


def _renders_on(name: str, kit: str, registry: Mapping[str, ComponentNode]) -> bool:
    """Whether ``name`` and its whole closure render on ``kit``."""
    return all(
        kit in registry[n].ui_kits for n in _component_closure(name, registry) if n in registry
    )


def selected_component_fragments(config: ProjectConfig) -> frozenset[str]:
    """The fragments of every selected component and the components they pull in."""
    components = list(getattr(config, "components", None) or [])
    if not components:
        return frozenset()
    from forge.components import COMPONENT_REGISTRY, component_fragment_name  # noqa: PLC0415

    names: set[str] = set()
    for name in components:
        names |= _component_closure(name, COMPONENT_REGISTRY)
    return frozenset(component_fragment_name(n) for n in names)


def component_fragments_for_kit(config: ProjectConfig, kit: str) -> frozenset[str]:
    """The component fragments an app on ``kit`` receives.

    A selected component lands in an app only with its whole closure: a
    component whose child the kit cannot render (``Console`` beside the
    shadcn-only ``StatCard``) is not applied to that app at all, rather than
    applied without the child its files import.
    """
    components = list(getattr(config, "components", None) or [])
    if not components:
        return frozenset()
    from forge.components import COMPONENT_REGISTRY, component_fragment_name  # noqa: PLC0415

    names: set[str] = set()
    for name in components:
        if _renders_on(name, kit, COMPONENT_REGISTRY):
            names |= _component_closure(name, COMPONENT_REGISTRY)
    return frozenset(component_fragment_name(n) for n in names)


def _check_component_ui_kits(
    config: ProjectConfig,
    selected: list[str],
    ordered: tuple[str, ...],
    registry: Mapping[str, ComponentNode],
) -> None:
    """Refuse selected components, or children they pull in, that no frontend's kit can render.

    A component is kept when at least one Vue frontend's kit renders it with
    its whole closure (it is then applied to the apps that can). Otherwise the
    refusal reports, against the primary's kit, each component that kit cannot
    render, through the selected component that needs it, with the component
    the kit offers instead when the manifest names one.
    """
    kits = _project_ui_kits(config)
    if not kits:
        return
    unplaced = [n for n in selected if not any(_renders_on(n, kit, registry) for kit in kits)]
    if not unplaced:
        return
    from forge.ui_kits import refusal  # noqa: PLC0415

    # Report only what no frontend can take: the closures of the unplaced
    # components. With one kit that is every component the kit cannot render.
    reported: set[str] = set()
    for name in unplaced:
        reported |= _component_closure(name, registry)
    ordered = tuple(n for n in ordered if n in reported)
    kit = kits[0]
    via: dict[str, str | None] = dict.fromkeys(selected)
    pending = list(selected)
    while pending:
        name = pending.pop()
        node = registry.get(name)
        for child in sorted(node.children) if node is not None else ():
            if child not in via:
                via[child] = via[name] or name
                pending.append(child)
    refused = [registry[name] for name in ordered if kit not in registry[name].ui_kits]
    if refused:
        raise OptionsError(
            " ".join(
                refusal(
                    "Component",
                    node.name,
                    node.ui_kits,
                    kit,
                    via=via.get(node.name),
                    replaced_by=node.replaced_by,
                )
                for node in refused
            ),
            code=OPTIONS_INVALID_VALUE,
            context={"ui_kit": kit, "components": [node.name for node in refused]},
        )


def _check_fragment_ui_kits(config: ProjectConfig, fragment_set: set[str]) -> None:
    """Refuse frontend fragments that declare kits none of the project's Vue frontends uses.

    A fragment some frontend's kit accepts is kept and applied to those apps only.
    """
    kits = _project_ui_kits(config)
    if not kits:
        return
    from forge.ui_kits import refusal  # noqa: PLC0415

    kit = ", ".join(kits)
    refused = sorted(
        name
        for name in fragment_set
        if FRAGMENT_REGISTRY[name].ui_kits
        and not any(k in FRAGMENT_REGISTRY[name].ui_kits for k in kits)
    )
    if refused:
        raise OptionsError(
            " ".join(
                refusal("Fragment", name, FRAGMENT_REGISTRY[name].ui_kits, kit) for name in refused
            ),
            code=OPTIONS_INVALID_VALUE,
            context={"ui_kit": kit, "fragments": refused},
        )


# A Helm release name: a DNS-1123 label of at most 53 characters.
_RELEASE_NAME = re.compile(r"^[a-z0-9]([-a-z0-9]{0,51}[a-z0-9])?$")
# Releases and Services of the infrastructure charts (CloudNativePG names its Services <cluster>-rw/-ro/-r).
_INFRA_CHART_NAMES = {
    "postgres": "the postgres chart",
    "postgres-rw": "the postgres chart",
    "postgres-ro": "the postgres chart",
    "postgres-r": "the postgres chart",
    "redis": "the redis chart",
    "keycloak": "the keycloak chart",
    "gatekeeper": "the gatekeeper chart",
}


def _check_service_chart_names(config: ProjectConfig) -> None:
    """deploy.helm_layout=per_service writes deploy/helm/<name>/ per backend and frontend, installed as release <name>
    beside the infrastructure charts: every name must be a release name, distinct from the others and from the
    infrastructure charts' releases and Services."""
    from forge.config._topology import compute_topology, helm_postgres_conflict  # noqa: PLC0415

    topology = compute_topology(config)
    names = [be["name"] for be in topology["backends"]] + [
        fe["name"] for fe in topology["frontends"]
    ]
    seen: set[str] = set(_INFRA_CHART_NAMES)
    for name in names:
        if not _RELEASE_NAME.match(name):
            raise OptionsError(
                f"deploy.helm_layout=per_service: {name!r} is not a Helm release name (lowercase letters, digits "
                "and '-', at most 53 characters); rename the service"
            )
        if name in seen:
            raise OptionsError(
                f"deploy.helm_layout=per_service: {name!r} is already taken by "
                f"{_INFRA_CHART_NAMES.get(name, 'another service')}; rename the service"
            )
        seen.add(name)
    conflict = helm_postgres_conflict(config, topology)
    if conflict:
        raise OptionsError(f"deploy.helm_layout=per_service: {conflict}; rename the service")


def resolve(config: ProjectConfig) -> ResolvedPlan:
    """Produce an ordered ResolvedPlan from ``config.options``.

    Called from ``ProjectConfig.validate()`` (eager validation) and
    again from ``generator.generate`` (canonical instance consumed by
    the injector).
    """
    project_backends = tuple(bc.language for bc in config.backends)
    _check_value_backend_support(config, project_backends)
    _check_option_allowed_backends(config, project_backends)
    _check_app_template_exclusions(config)
    _check_multitenancy_deferred(config)
    _check_go_multitenancy(config, project_backends)

    option_values = _apply_option_defaults(config.options)
    # The platform-auth gatekeeper stack depends on the keycloak + redis
    # services, which render only under ``include_keycloak``. The CLI builder
    # coerces ``auth.mode``→``none`` when keycloak is off so the compose stays
    # valid (no gatekeeper service with an undefined ``depends_on``); apply the
    # SAME coercion here so direct ProjectConfig/generate() construction (matrix
    # runner, headless fixtures, e2e) is covered too, not just the CLI path.
    #
    # The coercion is PROVIDER-AWARE: only the ``gatekeeper`` issuer actually
    # needs the keycloak + redis sidecars, so only it forces ``auth.mode``→
    # ``none`` when keycloak is off. Keycloak-free issuers (``in_memory`` mints
    # tokens in-process; ``oidc_generic`` points at an external IdP) must keep
    # ``auth.mode=generate`` so they remain usable without standing up keycloak.
    # ``auth.provider`` defaults to ``gatekeeper``, so the golden presets (which
    # never set an explicit provider) still coerce exactly as before — verified
    # byte-identical by the golden snapshots.
    if (
        not config.include_keycloak
        and option_values.get("auth.mode") == "generate"
        and _provider_needs_keycloak(option_values.get("auth.provider"))
    ):
        option_values["auth.mode"] = "none"
    # ``auth.provider`` is a sub-discriminator of ``auth.mode=generate`` — it
    # selects the token issuer for the generated auth stack. When the stack
    # isn't generated it must contribute no fragments, so coerce it to the
    # no-op ``none`` value (mirrors the auth.mode coercion above). This keeps
    # the provider's gatekeeper/oidc fragments from leaking into a no-auth
    # project while still defaulting to ``gatekeeper`` when auth IS generated.
    if option_values.get("auth.mode") != "generate" and "auth.provider" in option_values:
        option_values["auth.provider"] = "none"
    if option_values.get("auth.api_keys") and option_values.get("auth.provider") != "gatekeeper":
        raise OptionsError(
            "auth.api_keys requires include_keycloak=True, auth.mode=generate "
            "and auth.provider=gatekeeper."
        )
    fragment_set = _collect_fragments(option_values)
    fragment_set |= _collect_component_fragments(config)
    fragment_set = _expand_deps(fragment_set)
    # deploy.helm_layout picks how deploy.target=kubernetes packages the project: per_service puts one chart per
    # service in place of the umbrella chart, after every dependency is in, so the umbrella cannot come back.
    if (
        "deploy_helm_chart" in fragment_set
        and option_values.get("deploy.helm_layout") == "per_service"
    ):
        _check_service_chart_names(config)
        fragment_set.discard("deploy_helm_chart")
        fragment_set.add("deploy_helm_service_charts")
    _check_fragment_ui_kits(config, fragment_set)
    _check_security_constraints(option_values, fragment_set)
    _check_unverified_tenant(option_values)
    _check_self_registration(option_values)
    _validate_reads_options(fragment_set)
    _check_conflicts(fragment_set, project_backends)
    order = _topo_sort(fragment_set)

    resolved: list[ResolvedFragment] = []
    capabilities: set[str] = set()

    for name in order:
        frag = FRAGMENT_REGISTRY[name]
        targets = _target_backends(frag, project_backends)
        if not targets:
            # A project-scoped fragment gated on the active frontend (a Vue
            # component, an auth session-timeout fragment, …) applies to the
            # frontend app at apps/<slug>/ — NOT a backend — via a proxy impl.
            # Its backend target-set can be empty (no backend matches the proxy
            # impl's language, e.g. a Vue + Node-only project), but it must still
            # be applied. Keep it, targeting its (single) project-scoped impl
            # language so ``apply_project_features`` applies it exactly once.
            if (
                frag.target_frontends
                and any(fc.framework in frag.target_frontends for fc in config.active_frontends)
                and any(impl.scope == "project" for impl in frag.implementations.values())
            ):
                project_lang = next(
                    lang for lang, impl in frag.implementations.items() if impl.scope == "project"
                )
                resolved.append(ResolvedFragment(fragment=frag, target_backends=(project_lang,)))
                capabilities.update(frag.capabilities)
                continue
            # A fragment was pulled in (via option value or transitive
            # dep) but none of the project's backends support it. If the
            # user explicitly asked for the fragment (via an option
            # whose value maps to it), that's a hard error — they wanted
            # something that can't apply. If the fragment was pulled in
            # by a default Option value (they didn't touch it), skip
            # silently — that's just "this default isn't relevant here".
            #
            # ``option_origins`` distinguishes the two: an option with
            # origin="user" is a real user selection; origin="default"
            # is a resolver-filled value persisted in forge.toml on a
            # previous run. Empty / missing origins fall back to "user"
            # to preserve pre-WS2 behavior for ad-hoc ProjectConfig
            # construction without origins (test fixtures, in-memory
            # callers that haven't been ported).
            if _is_user_selected(config.options, name, config.option_origins):
                supported = ", ".join(sorted(lg.value for lg in frag.implementations)) or "(none)"
                present = ", ".join(lang.value for lang in project_backends) or "(none)"
                raise OptionsError(
                    f"Fragment '{name}' is requested but none of its supported "
                    f"backends ({supported}) are present in this project "
                    f"(backends: {present}).",
                    code=OPTIONS_INVALID_VALUE,
                    context={
                        "fragment": name,
                        "supported_backends": sorted(lg.value for lg in frag.implementations),
                        "project_backends": [lang.value for lang in project_backends],
                    },
                )
            continue
        resolved.append(ResolvedFragment(fragment=frag, target_backends=targets))
        capabilities.update(frag.capabilities)

    return ResolvedPlan(
        ordered=tuple(resolved),
        capabilities=frozenset(capabilities),
        option_values=option_values,
    )


def _is_user_selected(
    user_options: dict[str, object],
    fragment_name: str,
    origins: dict[str, str] | None = None,
) -> bool:
    """True if an option the user explicitly set enables this fragment.

    Used to distinguish "silent skip — default didn't apply here" from
    "hard error — user requested something impossible."

    ``origins`` maps each option path in ``user_options`` to ``"user"``
    (explicit user choice) or ``"default"`` (resolver-filled value
    persisted in ``forge.toml`` on a previous run). Missing keys and a
    ``None`` argument both fall back to ``"user"`` — preserving the
    pre-WS2 behavior where every path present in ``user_options`` was
    treated as a real user selection. Test fixtures and in-memory
    callers that build ``ProjectConfig`` without an origins map keep
    working unchanged.

    The discriminator-vs-single-fragment heuristic is unchanged: options
    that fan out to multiple per-language fragments (e.g.
    ``auth.mode=generate`` enables ``platform_auth_sdk_python`` +
    ``_node`` + ``_rust``) are NOT counted as user-selecting any single
    incompatible fragment — the user picked the *bundle*; per-language
    fanout is expected to be language-filtered silently. Single-fragment
    options still hard-error on incompatibility — a real user typo
    worth surfacing.
    """
    if origins is None:
        origins = {}
    for path, value in user_options.items():
        # Only paths the user explicitly set count. Defaulted-but-
        # persisted values look identical to user input in
        # ``user_options`` (the manifest read populates it from
        # forge.toml regardless of provenance); origins is the
        # signal we use to tell them apart.
        if origins.get(path, "user") != "user":
            continue
        spec = OPTION_REGISTRY.get(path)
        if spec is None:
            continue
        # LIST / STR / INT / OBJECT options never map values to fragments
        # (see _collect_fragments for the same short-circuit). Skipping
        # here also avoids dict.get() raising on unhashable list values.
        if not spec.enables:
            continue
        enabled_set = spec.enables.get(value, ())
        if fragment_name not in enabled_set:
            continue
        # Single-fragment enables = user opted into THIS fragment specifically.
        # Multi-fragment enables = discriminator/bundle; treat unmatched
        # entries as silent fanout-skips, not hard errors.
        if len(enabled_set) == 1:
            return True
    return False
