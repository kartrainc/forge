"""Fuzz tests for the resolver (P2.4).

Forge ships ~100+ options with combinatorial interactions via
``Option.enables``, ``Fragment.depends_on``, and
``Fragment.conflicts_with``. Hand-written tests cover a finite list of
presets (python-vue, node-svelte, ...); this module randomizes option
sets and verifies the resolver:

1. Always terminates (no infinite loops in the dep graph).
2. Either produces a valid plan or raises :class:`OptionsError`.
3. Never produces a plan that contains a conflict pair.
4. Never produces a plan with a dangling ``depends_on`` (a required
   fragment missing from the plan).

Hypothesis drives the random generation. The test is registered under
a ``fuzz`` marker so it can be opted out of the fast suite and run
nightly instead.

Initiative #9 — generator expansion. Pre-#9 the fuzz only set BOOL +
ENUM options and exercised a single-backend project. Real users hit
LIST options (``connectors.backends`` today; the registry will grow
more), OBJECT options (none today but the registry supports them),
multi-backend projects (python + node, python + node + rust), and
frontend swaps (vue / svelte / flutter / none). Adding those axes to
the fuzz catches resolver interactions that the hand-written matrix
preset doesn't cover — e.g. a python+node multi-backend with a
python-only option enabled used to slip through with the resolver
silently leaving the node service un-touched.
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from forge.capability_resolver import resolve
from forge.config import (
    BackendConfig,
    BackendLanguage,
    FrontendConfig,
    FrontendFramework,
    ProjectConfig,
)
from forge.errors import OptionsError
from forge.fragments import FRAGMENT_REGISTRY
from forge.options import OPTION_REGISTRY, OptionType

pytestmark = pytest.mark.fuzz


def _option_value_strategy(option):
    """Return a Hypothesis strategy that produces values of this option's type.

    We sample realistically-typed values so the resolver doesn't reject
    everything at coercion. Unknown / exotic option types fall back to
    the option's registered default.

    Initiative #9 — extended to handle LIST (1-3 inner elements) and
    OBJECT (shallow dict matching the option's declared
    ``object_schema``). The pre-#9 implementation fell through to
    ``st.just(option.default)`` for LIST + OBJECT, so the fuzz only
    ever exercised the registered defaults of those option types
    (today: ``connectors.backends=[]``); a regression in
    LIST-coercion or OBJECT-shape-validation wouldn't have surfaced.
    """
    if option.type == OptionType.BOOL:
        return st.booleans()
    if option.type == OptionType.ENUM:
        choices = list(getattr(option, "choices", ()) or getattr(option, "options", ()) or ())
        if choices:
            return st.sampled_from(choices)
        return st.just(option.default)
    if option.type == OptionType.INT:
        # Honour declared bounds so the resolver doesn't reject every
        # sample at coercion. Falls back to a wide range when no bounds
        # are set — most INT options accept any non-negative integer.
        lo = getattr(option, "min", None) or 0
        hi = getattr(option, "max", None) or 1000
        return st.integers(min_value=lo, max_value=hi)
    if option.type == OptionType.STR:
        return st.text(alphabet="abcdef0123456789-_", min_size=1, max_size=10)
    if option.type == OptionType.LIST:
        # Prefer the option's known valid-value set when discoverable.
        # The Option dataclass doesn't formally declare an "inner enum"
        # for LIST today (the ``connectors.backends`` description names
        # ``http/fs/sql/s3/mcp`` but the registry has no structured
        # field for that), so the best signal we have is the registered
        # default when it carries example values. Falling back to short
        # identifiers keeps the strategy non-empty for options that
        # default to ``[]``. Initiative #9 follow-up (out of scope):
        # extend Option to declare ``list_inner_options`` so this
        # strategy can pick from a known-good set instead of guessing —
        # codex review flagged this as a place where the fuzz currently
        # bless values the downstream template can't safely consume.
        default = list(getattr(option, "default", []) or [])
        if default:
            return st.lists(
                st.sampled_from(default),
                min_size=1,
                max_size=min(3, len(default)),
                unique=True,
            )
        return st.lists(
            st.text(alphabet="abcdef", min_size=1, max_size=6),
            min_size=1,
            max_size=3,
        )
    if option.type == OptionType.OBJECT:
        # Phase C OBJECT options declare ``object_schema``. When present,
        # synthesize a dict whose keys match the spec exactly and whose
        # values match each per-key type — anything else would fail
        # ``Option._validate_object_shape`` and the test would always
        # short-circuit on coercion failure rather than exercising the
        # resolver. When ``object_schema`` is absent, fall back to a
        # generic ``{key: str}`` strategy that passes the outer-dict
        # check.
        schema = getattr(option, "object_schema", None) or {}
        if schema:
            return _object_strategy_from_schema(schema)
        return st.dictionaries(
            st.text(alphabet="abcdef", min_size=1, max_size=6),
            st.text(alphabet="abcdef", min_size=1, max_size=6),
            min_size=0,
            max_size=3,
        )
    return st.just(option.default)


def _object_strategy_from_schema(schema):
    """Build a Hypothesis strategy producing a dict that satisfies
    every required key of an ``object_schema`` (Phase C).

    Per-key types are honoured: BOOL → bool, INT → int (0-100),
    STR → short string, LIST → 0-3 string elements, ENUM → one of
    the spec's declared options. Optional keys are produced with 50%
    probability so the fuzz exercises both the "all keys present"
    and "only required keys" branches of
    :func:`Option._validate_object_shape`.
    """

    @st.composite
    def _strategy(draw):
        out: dict[str, object] = {}
        for key, spec in schema.items():
            if not spec.required and draw(st.booleans()):
                continue
            if spec.type == OptionType.BOOL:
                out[key] = draw(st.booleans())
            elif spec.type == OptionType.INT:
                out[key] = draw(st.integers(min_value=0, max_value=100))
            elif spec.type == OptionType.STR:
                out[key] = draw(st.text(alphabet="abcdef", min_size=1, max_size=6))
            elif spec.type == OptionType.LIST:
                out[key] = draw(
                    st.lists(
                        st.text(alphabet="abcdef", min_size=1, max_size=4),
                        min_size=0,
                        max_size=3,
                    )
                )
            elif spec.type == OptionType.ENUM and spec.options:
                out[key] = draw(st.sampled_from(list(spec.options)))
            else:
                out[key] = spec.default
        return out

    return _strategy()


# Pre-#9 only BOOL + ENUM. #9 extends to LIST + OBJECT so the fuzz
# exercises the resolver's coercion + validation paths for those
# kinds, which today only fire from the single registered
# ``connectors.backends`` LIST option (and would never fire for OBJECT
# until the registry adds one).
_FUZZABLE_OPTIONS = [
    opt
    for opt in OPTION_REGISTRY.values()
    if opt.type
    in (OptionType.BOOL, OptionType.ENUM, OptionType.LIST, OptionType.OBJECT)
]


_BACKEND_LANGUAGES = [
    BackendLanguage.PYTHON,
    BackendLanguage.NODE,
    BackendLanguage.RUST,
]


# Initiative #9 — frontend swap strategy.  Pre-#9 the fuzz never
# attached a frontend, so every random sample exercised "backend-only"
# resolution. ``NONE`` is the existing default and ``flutter`` covers
# the openapi-required path that has tripped resolver coherence
# checks repeatedly.
_FRONTEND_FRAMEWORKS: list[FrontendFramework | None] = [
    None,
    FrontendFramework.VUE,
    FrontendFramework.SVELTE,
    FrontendFramework.FLUTTER,
]


def _frontend_for(framework: FrontendFramework | None) -> FrontendConfig | None:
    """Build a minimal ``FrontendConfig`` for the chosen framework, or
    ``None`` to model the backend-only case.

    Flutter needs OpenAPI on by contract (FrontendConfig.validate
    enforces this when language is dart-the-app-bundler), so we set
    it unconditionally. The other frameworks accept either.
    """
    if framework is None:
        return None
    return FrontendConfig(
        project_name="fuzz",
        framework=framework,
        include_auth=False,
        include_chat=False,
        include_openapi=framework == FrontendFramework.FLUTTER,
        # Distinct port so the multi-backend ports stay collision-free
        # against the frontend's dev server.
        server_port=5179,
    )


def _backend(name: str, language: BackendLanguage, port: int) -> BackendConfig:
    """Construct a minimal, valid ``BackendConfig`` for the language."""
    return BackendConfig(
        name=name,
        language=language,
        features=["items"],
        server_port=port,
        description=f"fuzz {language.value} backend",
    )


@st.composite
def random_project_config(draw):
    """Generate a :class:`ProjectConfig` with a random subset of fuzzable
    options set, a random backend-language combination (single or
    multi-backend), and a random frontend (or none).

    Initiative #9 axes:
      * **LIST + OBJECT options** — see :func:`_option_value_strategy`.
      * **Multi-backend** — 1-3 backends drawn from {python, node,
        rust}, with distinct names + ports.
      * **Frontend swap** — None / vue / svelte / flutter.

    The pre-#9 single-axis fuzz (BOOL/ENUM, single python/node/rust
    backend, no frontend) is a strict subset of this generator; the
    consistency assertions in :func:`_assert_plan_is_consistent`
    still hold across the expanded surface.
    """
    sample_size = draw(st.integers(min_value=0, max_value=min(20, len(_FUZZABLE_OPTIONS))))
    chosen = draw(
        st.lists(
            st.sampled_from(_FUZZABLE_OPTIONS) if _FUZZABLE_OPTIONS else st.nothing(),
            min_size=sample_size,
            max_size=sample_size,
            unique_by=lambda o: o.path,
        )
    )
    option_values: dict[str, object] = {}
    for option in chosen:
        option_values[option.path] = draw(_option_value_strategy(option))

    # Multi-backend axis: 1-3 backends sampled WITHOUT replacement so
    # each language appears at most once (matches the matrix scenario
    # set; mixing two pythons is supported but increases port-collision
    # complexity without strengthening the fuzz).
    n_backends = draw(st.integers(min_value=1, max_value=3))
    languages = draw(
        st.lists(
            st.sampled_from(_BACKEND_LANGUAGES),
            min_size=n_backends,
            max_size=n_backends,
            unique=True,
        )
    )
    backends = [
        _backend(
            name=f"svc-{i}-{lang.value}",
            language=lang,
            # Spread ports so two backends in the same fuzz sample
            # don't collide; offset starts at 5000 + i*10 to leave room
            # for the frontend's default 5173.
            port=5000 + i * 10,
        )
        for i, lang in enumerate(languages)
    ]

    framework = draw(st.sampled_from(_FRONTEND_FRAMEWORKS))
    frontend = _frontend_for(framework)

    return ProjectConfig(
        project_name="fuzz",
        backends=backends,
        frontends=([frontend] if frontend is not None else []),
        options=option_values,
    )


@settings(
    deadline=None,
    max_examples=75,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)
@given(config=random_project_config())
def test_resolve_either_succeeds_cleanly_or_raises_options_error(config):
    """The resolver must terminate on every random input and either
    return a plan or raise :class:`OptionsError` / :class:`ValueError`.
    Any other exception is a bug.

    Initiative #9: ``ValueError`` is admitted alongside
    ``OptionsError`` because the expanded multi-backend + frontend +
    LIST/OBJECT surface routinely produces combinations that fail
    structural validation (port collisions, frontend-mode coherence,
    OBJECT shape mismatches) that surface as ``ValueError`` from
    ``ProjectConfig.validate`` / ``Option._validate_value`` before
    the resolver gets to coercion. Both error types are the expected
    "invalid input rejected gracefully" outcome; only a SystemExit or
    a bare Exception means the resolver crashed.
    """
    # Validate first — same as the CLI's builder path. ``ProjectConfig``
    # carries dataclass defaults that may fail validation independently
    # of the resolver (e.g. flutter + openapi=False, port collision
    # between a random backend port and the frontend default 5173).
    # Catching here matches what the runtime CLI does and prevents
    # the resolver from being asked to plan an invalid config.
    try:
        config.validate()
    except (ValueError, OptionsError):
        return  # expected for invalid combinations
    # Second resolve — narrow exception set. ``config.validate()`` above
    # already ran ``_validate_options`` + the resolver once and wrapped
    # any failure as ValueError, so a fresh ValueError here would more
    # likely be a resolver bug (option-driven sub-resolution diverging
    # on the second call) than expected invalid input. We accept only
    # OptionsError — the resolver's explicit "config is invalid"
    # signal — and let any other exception propagate. Codex review
    # flagged the prior ``(OptionsError, ValueError)`` catch as too
    # lenient.
    plan = resolve(config)
    _assert_plan_is_consistent(plan)


def _assert_plan_is_consistent(plan) -> None:
    """Invariants every returned plan must satisfy."""
    names = {rf.fragment.name for rf in plan.ordered}
    # All declared depends_on must be present in the plan.
    for rf in plan.ordered:
        missing = [d for d in rf.fragment.depends_on if d not in names]
        assert not missing, (
            f"fragment {rf.fragment.name!r} depends on {missing} which are "
            "absent from the resolved plan"
        )
    # No pair in the plan may conflict.
    for rf in plan.ordered:
        conflicts = set(rf.fragment.conflicts_with) & names
        assert not conflicts, (
            f"fragment {rf.fragment.name!r} conflicts with {sorted(conflicts)} "
            "but both ended up in the plan"
        )
    # Capabilities should match the fragments in the plan.
    claimed = set()
    for rf in plan.ordered:
        claimed.update(rf.fragment.capabilities)
    extra = set(plan.capabilities) - claimed
    assert not extra, (
        f"plan.capabilities contains {extra} not sourced from any fragment"
    )


@pytest.mark.parametrize(
    "fragment_name",
    sorted(FRAGMENT_REGISTRY.keys()),
)
def test_every_registered_fragment_has_non_empty_implementations(fragment_name):
    """Cheap parametric sanity check — catches a fragment accidentally
    registered with an empty impl dict."""
    fragment = FRAGMENT_REGISTRY[fragment_name]
    assert fragment.implementations, (
        f"fragment {fragment_name!r} has no implementations"
    )


# ----------------------------------------------------------------------
# Initiative #9 — explicit smoke checks against the expanded surface.
# These complement the Hypothesis-driven test above by pinning a few
# specific shapes that previously bypassed fuzz coverage. A regression
# in LIST coercion / multi-backend / frontend-swap would surface here
# even if Hypothesis's random sampler happened not to hit the right
# combination this run.
# ----------------------------------------------------------------------


class TestExpandedFuzzSurface:
    """Pinned smoke samples for the Initiative #9 fuzz expansions.

    The Hypothesis-driven test above samples 75 combinations; this
    class adds deterministic spot-checks for the four expansion axes
    so a CI run that happened to draw zero multi-backend / zero LIST /
    zero OBJECT / zero frontend samples still flags a regression.
    """

    def test_list_option_value_is_accepted(self):
        """A LIST option (``connectors.backends``) set to a small list
        of strings must pass ``Option._validate_value`` and feed into
        the resolver without exception. Pre-#9 the fuzz only set the
        registered default (``[]``), so a regression that broke LIST
        coercion for non-empty values would not have surfaced."""
        opt = OPTION_REGISTRY["connectors.backends"]
        opt.validate_value(["http", "fs"])
        config = ProjectConfig(
            project_name="fuzz-list",
            backends=[_backend("api", BackendLanguage.PYTHON, 5000)],
            options={"connectors.backends": ["http", "fs"], "connectors.enabled": True},
        )
        config.validate()  # must not raise.
        resolve(config)  # must not raise.

    def test_multi_backend_plan_is_consistent(self):
        """A python + node + rust project must resolve to a consistent
        plan. Pre-#9 the fuzz only sampled single-backend configs;
        a multi-backend resolver regression (e.g. forgetting to apply
        a fragment to the second backend) would slip through."""
        config = ProjectConfig(
            project_name="fuzz-multi",
            backends=[
                _backend("py", BackendLanguage.PYTHON, 5000),
                _backend("ts", BackendLanguage.NODE, 5010),
                _backend("rs", BackendLanguage.RUST, 5020),
            ],
        )
        config.validate()
        plan = resolve(config)
        _assert_plan_is_consistent(plan)

    @pytest.mark.parametrize(
        "framework",
        [
            FrontendFramework.VUE,
            FrontendFramework.SVELTE,
            FrontendFramework.FLUTTER,
        ],
    )
    def test_frontend_swap_resolves(self, framework: FrontendFramework):
        """Each shipped frontend framework must compose with a python
        backend without exception. Pre-#9 the fuzz never attached a
        frontend; a resolver coherence regression (e.g. a fragment
        whose conflicts_with depended on the frontend choice) would
        not have surfaced."""
        config = ProjectConfig(
            project_name="fuzz-fe",
            backends=[_backend("api", BackendLanguage.PYTHON, 5000)],
            frontends=[_frontend_for(framework)],
        )
        config.validate()
        plan = resolve(config)
        _assert_plan_is_consistent(plan)


def _stub_object_option():
    """Build an in-memory OBJECT-typed Option for the OBJECT-strategy
    smoke check below. Avoid registering it (would pollute
    OPTION_REGISTRY for other tests in this session) — the validation
    surface is exercised against the Option instance directly.
    """
    from forge.options._registry import (  # noqa: PLC0415
        FeatureCategory,
        ObjectFieldSpec,
        Option,
    )

    return Option(
        path="fuzz_only.object_demo",
        type=OptionType.OBJECT,
        default={"name": "default"},
        summary="Test-only OBJECT option for fuzz strategy.",
        description="Not registered globally — instantiated for direct validation.",
        category=FeatureCategory.PLATFORM,
        stability="experimental",
        object_schema={
            "name": ObjectFieldSpec(type=OptionType.STR, required=True),
            "count": ObjectFieldSpec(type=OptionType.INT, required=False, default=0),
            "tags": ObjectFieldSpec(type=OptionType.LIST, required=False),
        },
    )


class TestObjectStrategy:
    """The OBJECT-typed option strategy must produce dicts that pass
    ``Option.validate_value`` against the option's declared schema.

    Driven via Hypothesis ``@given`` so the test is shrinkable and
    parallel-safe — the imperative ``strategy.example()`` form
    triggers a NonInteractiveExampleWarning and skips Hypothesis's
    counterexample-shrinking on failure.
    """

    @settings(deadline=None, max_examples=30)
    @given(data=st.data())
    def test_object_strategy_dicts_validate(self, data):
        opt = _stub_object_option()
        strategy = _option_value_strategy(opt)
        sample = data.draw(strategy)
        # Required keys present?
        assert "name" in sample, f"OBJECT sample missing required key: {sample!r}"
        # Per-key types match the schema?
        assert isinstance(sample["name"], str)
        if "count" in sample:
            assert isinstance(sample["count"], int) and not isinstance(sample["count"], bool)
        if "tags" in sample:
            assert isinstance(sample["tags"], list)
        # The Option's own validator agrees.
        opt.validate_value(sample)
