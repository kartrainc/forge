"""Parity tier ↔ implementations consistency is enforced at construction (P2.1).

Before P2.1 the tier/impl contract was enforced only by
``tests/test_fragment_parity.py`` — useful for first-party fragments
but silent for plugin-shipped fragments (they'd only fail when a
developer ran forge's own test suite). Moving the check into
``Fragment.__post_init__`` means plugin authors see parity errors at
plugin-load time, which is when they're best positioned to fix them.
"""

from __future__ import annotations

import pytest

from forge.config import BackendLanguage
from forge.errors import FragmentError
from forge.fragments import Fragment, FragmentImplSpec


def _impl(name: str = "demo") -> FragmentImplSpec:
    return FragmentImplSpec(fragment_dir=f"_fragments/{name}")


class TestTier1Validation:
    def test_tier_1_covering_all_built_ins_is_allowed(self) -> None:
        Fragment(
            name="demo_t1_ok",
            parity_tier=1,
            implementations={
                BackendLanguage.PYTHON: _impl(),
                BackendLanguage.NODE: _impl(),
                BackendLanguage.RUST: _impl(),
                BackendLanguage.GO: _impl(),
            },
        )

    def test_tier_1_missing_rust_raises(self) -> None:
        with pytest.raises(FragmentError) as exc:
            Fragment(
                name="demo_t1_missing_rust",
                parity_tier=1,
                implementations={
                    BackendLanguage.PYTHON: _impl(),
                    BackendLanguage.NODE: _impl(),
                },
            )
        assert "rust" in str(exc.value)

    def test_tier_1_python_only_raises_with_all_missing(self) -> None:
        with pytest.raises(FragmentError) as exc:
            Fragment(
                name="demo_t1_python_only",
                parity_tier=1,
                implementations={BackendLanguage.PYTHON: _impl()},
            )
        assert "node" in str(exc.value) and "rust" in str(exc.value)


class TestTier3Validation:
    def test_tier_3_python_only_is_allowed(self) -> None:
        Fragment(
            name="demo_t3_ok",
            parity_tier=3,
            implementations={BackendLanguage.PYTHON: _impl()},
        )

    def test_tier_3_with_node_impl_raises(self) -> None:
        with pytest.raises(FragmentError) as exc:
            Fragment(
                name="demo_t3_has_node",
                parity_tier=3,
                implementations={
                    BackendLanguage.PYTHON: _impl(),
                    BackendLanguage.NODE: _impl(),
                },
            )
        assert "tier 3" in str(exc.value).lower()


class TestTier2Validation:
    def test_tier_2_python_only_is_allowed(self) -> None:
        # Migration target: explicitly tier 2 ahead of the impls landing.
        Fragment(
            name="demo_t2_pending",
            parity_tier=2,
            implementations={BackendLanguage.PYTHON: _impl()},
        )

    def test_tier_2_mixed_is_allowed(self) -> None:
        Fragment(
            name="demo_t2_partial",
            parity_tier=2,
            implementations={
                BackendLanguage.PYTHON: _impl(),
                BackendLanguage.NODE: _impl(),
            },
        )


class TestAutoDerivationUnaffected:
    def test_no_explicit_tier_uses_auto(self) -> None:
        frag = Fragment(
            name="demo_auto_t1",
            implementations={
                BackendLanguage.PYTHON: _impl(),
                BackendLanguage.NODE: _impl(),
                BackendLanguage.RUST: _impl(),
                BackendLanguage.GO: _impl(),
            },
        )
        assert frag.parity_tier == 1

    def test_auto_python_only_is_tier_3(self) -> None:
        frag = Fragment(
            name="demo_auto_t3",
            implementations={BackendLanguage.PYTHON: _impl()},
        )
        assert frag.parity_tier == 3
