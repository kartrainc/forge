"""Enforce RFC-006 (cross-backend fragment parity contract).

Each registered fragment must satisfy the tier/implementation contract:

- tier 1 ⇒ every built-in backend (Python, Node, Rust, Go) has an impl.
- tier 3 ⇒ only Python has an impl.
- tier 2 ⇒ the residual (some subset, but not tier 1 or tier 3).

These invariants are asserted against the live ``FRAGMENT_REGISTRY``
so a PR that drops a backend from a tier-1 fragment, or promotes a
Python-only fragment without filling in the other backends, fails CI.

The tier is auto-derived in ``Fragment.__post_init__`` when unset, so
these tests mostly lock in the derivation. An author can override
``parity_tier`` explicitly (e.g. to flag a tier-2 migration target
that ships only Python today) — the override paths also flow through
here, so an inconsistent explicit label is caught too.
"""

from __future__ import annotations

import pytest

from forge.config import BackendLanguage
from forge.fragments import FRAGMENT_REGISTRY, Fragment

BUILT_INS = set(BackendLanguage)


def _built_in_impl_langs(fragment: Fragment) -> set[BackendLanguage]:
    return {lang for lang in fragment.implementations if lang in BUILT_INS}


def test_every_fragment_has_concrete_tier() -> None:
    """After ``__post_init__`` no fragment should leave ``parity_tier``
    as ``None`` — auto-derivation runs for everyone."""
    for name, frag in FRAGMENT_REGISTRY.items():
        assert frag.parity_tier in (1, 2, 3), (
            f"fragment {name!r} has parity_tier={frag.parity_tier!r}; "
            "should be 1, 2, or 3 after __post_init__"
        )


@pytest.mark.parametrize(
    "fragment",
    [f for f in FRAGMENT_REGISTRY.values() if f.parity_tier == 1],
    ids=lambda f: f.name,
)
def test_tier_one_covers_all_built_in_backends(fragment: Fragment) -> None:
    """A tier-1 fragment claims mandatory cross-backend parity. The
    registry must back that up with an implementation for every
    built-in backend. If this fails, either add the missing impl or
    drop the tier to 2 with a CHANGELOG note."""
    covered = _built_in_impl_langs(fragment)
    missing = BUILT_INS - covered
    assert not missing, (
        f"tier-1 fragment {fragment.name!r} is missing built-in impls: "
        f"{sorted(m.value for m in missing)}"
    )


@pytest.mark.parametrize(
    "fragment",
    [f for f in FRAGMENT_REGISTRY.values() if f.parity_tier == 3],
    ids=lambda f: f.name,
)
def test_tier_three_is_python_only(fragment: Fragment) -> None:
    """A tier-3 fragment is Python-only by contract. A Node or Rust
    impl shipping means the fragment has graduated to tier 2 (or tier
    1 if all three are present) and the label is stale."""
    covered = _built_in_impl_langs(fragment)
    assert covered == {BackendLanguage.PYTHON}, (
        f"tier-3 fragment {fragment.name!r} ships impls for "
        f"{sorted(c.value for c in covered)}; should be python-only "
        "(bump to tier 2 if this is deliberate)"
    )


@pytest.mark.parametrize(
    "fragment",
    [f for f in FRAGMENT_REGISTRY.values() if f.parity_tier == 2],
    ids=lambda f: f.name,
)
def test_tier_two_does_not_cover_all_built_ins(fragment: Fragment) -> None:
    """Tier 2 cannot cover every built-in backend — a fragment that
    does is tier 1 by definition, so the explicit label contradicts
    the impl list. The inverse case (tier-2 with only Python) is
    allowed: it represents a committed migration target, explicitly
    promoted above the default (auto-derived) tier 3 by the author.
    See RFC-006 — ``queue_port`` / ``queue_redis`` / ``queue_sqs`` are
    the canonical examples."""
    covered = _built_in_impl_langs(fragment)
    assert covered != BUILT_INS, (
        f"tier-2 fragment {fragment.name!r} covers all built-in backends "
        "— should auto-derive as tier 1"
    )


def test_tier_distribution_is_reasonable() -> None:
    """Sanity check: we expect a non-trivial number of each tier.
    Catches accidental mass-retagging (e.g. if someone flipped the
    default to tier 1 and every fragment suddenly became tier 1)."""
    tiers: dict[int, int] = {1: 0, 2: 0, 3: 0}
    for frag in FRAGMENT_REGISTRY.values():
        assert frag.parity_tier is not None
        tiers[frag.parity_tier] += 1
    # Project has tier-1 middleware (rate_limit, security_headers,
    # observability, etc.) and a stack of tier-3 AI features. Both
    # should be represented.
    assert tiers[1] >= 3, f"too few tier-1 fragments: {tiers}"
    assert tiers[3] >= 3, f"too few tier-3 fragments: {tiers}"


# -- Filesystem-layout invariant (Epic 2 cheap path) --------------------------
#
# The dataclass-level checks above only enforce that ``Fragment.implementations``
# is keyed correctly. A tier-1 fragment that claims Python+Node+Rust impls but
# ships no actual ``files/<backend>/`` directory under
# ``forge/templates/_fragments/<name>/`` would still be applied at generate
# time and only fail at the plan_validator step (or worse, mid-generation if
# only ``inject.yaml`` is missing). Catching these here keeps tier-1 honesty
# enforced at PR time across both the dataclass and disk surfaces.


@pytest.mark.parametrize(
    "fragment",
    [f for f in FRAGMENT_REGISTRY.values() if f.parity_tier == 1],
    ids=lambda f: f.name,
)
def test_tier_one_has_filesystem_dirs_for_all_built_ins(fragment: Fragment) -> None:
    """Each tier-1 fragment's ``implementations[lang].fragment_dir`` must
    resolve to a real directory under ``forge/templates/_fragments/``.

    A tier-1 fragment must ship physical content for each built-in
    backend it claims to cover — otherwise generation lands an empty
    no-op for that backend, which violates the parity contract more
    quietly than the impl-key check above.
    """
    from forge.fragments import _resolve_fragment_dir

    missing: list[tuple[str, str]] = []
    for lang in BUILT_INS:
        impl = fragment.implementations.get(lang)
        if impl is None:
            continue  # already caught by test_tier_one_covers_all_built_in_backends
        frag_dir = _resolve_fragment_dir(impl.fragment_dir)
        if not frag_dir.is_dir():
            missing.append((lang.value, str(frag_dir)))
    assert not missing, (
        f"tier-1 fragment {fragment.name!r} declares impls whose fragment_dir "
        f"does not exist on disk: {missing}"
    )
