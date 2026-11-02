"""RFC-011 acceptance (1.2.0) — three-frontend coverage lint.

Walks every fragment shipping a Vue composable (a ``.vue`` file or a
composable ``.ts`` under a ``vue/`` directory) and asserts that a
sibling Svelte AND Flutter implementation exists in the same
``forge/features/<area>/templates/`` parent directory — or, failing
that, that the fragment carries an explicit
``Fragment.frontend_skip_reason`` marker.

The lint exists because RFC-011 (Accepted, 1.2.0) explicitly tolerates
the Vue/Svelte (``@hey-api/openapi-ts`` + TanStack Query) vs Flutter
(Retrofit + Riverpod) toolchain asymmetry but commits to NOT letting
that asymmetry silently grow per-fragment. Today's two Vue-shipping
fragments (``mcp_ui`` and ``platform_auth_session_timeout_vue``) both
have explicit Svelte and Flutter siblings; this test locks that in so
the next contributor who lands a Vue-only addition is forced to either
ship the siblings or document the asymmetry on the Fragment dataclass.

Sibling-name resolution mirrors the existing naming convention in the
features tree:

- ``platform_auth_session_timeout_vue`` ⇒ strip ``_vue`` to get
  ``platform_auth_session_timeout``, then look for
  ``platform_auth_session_timeout_svelte`` and
  ``platform_auth_session_timeout_flutter``.
- ``mcp_ui`` (no ``_vue`` suffix; Vue is the implicit default for
  legacy fragments) ⇒ base name is ``mcp_ui`` itself, then look for
  ``mcp_ui_svelte`` and ``mcp_ui_flutter``.

The escape hatch ``Fragment.frontend_skip_reason`` is plumbed through
``forge/fragments/_spec.py`` in the same 1.2.0 cycle. No fragment uses
it yet; that's a deliberate baseline so the first use is a conscious,
reviewed decision.

This file is a **registry-level invariant** alongside the other invariants
in ``tests/test_options.py`` and ``tests/test_fragment_parity.py``.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import pytest

from forge.fragments import FRAGMENT_REGISTRY

# Repo root: this file lives at ``tests/test_three_frontend_coverage.py``;
# the features tree is at ``forge/features/<area>/templates/<fragment>/``.
REPO_ROOT = Path(__file__).resolve().parent.parent
FEATURES_ROOT = REPO_ROOT / "forge" / "features"


def _ships_vue_composable(fragment_dir: Path) -> bool:
    """Return True iff the fragment directory ships any Vue surface.

    Two markers count:

    1. **Any ``.vue`` file** anywhere under the fragment directory.
       Catches the common case (e.g. ``mcp_ui`` shipping
       ``files/src/features/mcp/ToolRegistry.vue``).

    2. **A ``.ts`` file under a directory named ``vue``** (the layout
       the RFC-011 plan section anticipates for future per-frontend
       fragment organisation, e.g. ``templates/foo/frontends/vue/
       useFoo.ts``). The directory match is by basename so it works
       at any nesting depth.

    Tests/fixtures/golden files do not get a special pass — fragments
    aren't expected to ship a ``test/`` directory, and any ``.vue``
    under one would be a layout bug worth flagging.
    """
    if not fragment_dir.is_dir():
        return False
    for path in fragment_dir.rglob("*.vue"):
        if path.is_file():
            return True
    for path in fragment_dir.rglob("*.ts"):
        if path.is_file() and "vue" in {parent.name for parent in path.parents}:
            return True
    return False


def _sibling_base_name(fragment_dir_name: str) -> str:
    """Strip the ``_vue`` suffix if present; else return the name.

    ``platform_auth_session_timeout_vue`` → ``platform_auth_session_timeout``
    ``mcp_ui`` → ``mcp_ui``
    """
    if fragment_dir_name.endswith("_vue"):
        return fragment_dir_name[: -len("_vue")]
    return fragment_dir_name


def _iter_vue_shipping_fragments() -> Iterable[tuple[str, str, Path]]:
    """Yield ``(area, fragment_dir_name, fragment_dir_path)`` for every
    fragment in the features tree that ships a Vue composable.

    ``area`` is the directory name under ``forge/features/`` (e.g.
    ``auth``, ``platform``). Used both to scope sibling lookup (siblings
    must live in the SAME features area — a Svelte fragment in a
    different area is not a sibling) and to disambiguate identically-
    named fragments across areas (none today, but defensive).
    """
    if not FEATURES_ROOT.is_dir():
        return
    for area_dir in sorted(FEATURES_ROOT.iterdir()):
        if not area_dir.is_dir() or area_dir.name.startswith("_") or area_dir.name == "__pycache__":
            continue
        templates_dir = area_dir / "templates"
        if not templates_dir.is_dir():
            continue
        for fragment_dir in sorted(templates_dir.iterdir()):
            if not fragment_dir.is_dir():
                continue
            if _ships_vue_composable(fragment_dir):
                yield area_dir.name, fragment_dir.name, fragment_dir


def _has_sibling_directory(area: str, sibling_name: str) -> bool:
    """Return True iff ``forge/features/<area>/templates/<sibling_name>``
    exists as a directory."""
    return (FEATURES_ROOT / area / "templates" / sibling_name).is_dir()


def _fragment_skip_reason(fragment_dir_name: str) -> str | None:
    """Return the ``frontend_skip_reason`` for a fragment, if any.

    Looks the fragment up in ``FRAGMENT_REGISTRY`` by its directory
    name (which by convention matches the registered ``Fragment.name``
    for every fragment in the features tree today). Returns ``None``
    when the fragment is unregistered (impossible in CI but defensive
    for partial dev checkouts) or when no skip reason was set.
    """
    fragment = FRAGMENT_REGISTRY.get(fragment_dir_name)
    if fragment is None:
        return None
    reason = fragment.frontend_skip_reason
    if reason is not None and reason.strip():
        return reason
    return None


def _gather_violations() -> list[tuple[str, str, list[str], str | None]]:
    """Return a list of ``(area, vue_fragment, missing_siblings, skip_reason)``
    for every Vue-shipping fragment that lacks one or both siblings.

    ``missing_siblings`` is a list like ``['svelte']``, ``['flutter']``,
    or ``['svelte', 'flutter']``. ``skip_reason`` is the value of
    ``Fragment.frontend_skip_reason`` when the fragment opted out
    (in which case the entry is informational — the test passes for
    that fragment).
    """
    violations: list[tuple[str, str, list[str], str | None]] = []
    for area, vue_fragment, _vue_dir in _iter_vue_shipping_fragments():
        base = _sibling_base_name(vue_fragment)
        missing: list[str] = []
        for sibling_suffix in ("_svelte", "_flutter"):
            sibling_name = f"{base}{sibling_suffix}"
            if not _has_sibling_directory(area, sibling_name):
                missing.append(sibling_suffix.lstrip("_"))
        if not missing:
            continue
        violations.append((area, vue_fragment, missing, _fragment_skip_reason(vue_fragment)))
    return violations


# -- Discovery sanity checks --------------------------------------------------


class TestDiscovery:
    """Lock in that the walker actually finds the Vue-shipping fragments
    we know exist. Without this, the main lint could pass vacuously
    (zero fragments inspected) after a refactor that moves the features
    tree without updating ``FEATURES_ROOT``."""

    def test_features_root_exists(self) -> None:
        assert FEATURES_ROOT.is_dir(), (
            f"Expected features tree at {FEATURES_ROOT}; did the layout move?"
        )

    def test_walker_finds_known_vue_fragments(self) -> None:
        found = {name for _area, name, _path in _iter_vue_shipping_fragments()}
        # Baseline circa RFC-011 acceptance (1.2.0). If a future PR
        # removes one of these intentionally, update the set; if it
        # adds new Vue-shipping fragments, the main coverage test will
        # catch any missing siblings.
        # The session-timeout composable ships no Vue file since its dialog
        # became a fragment of its own (ADR-015); the dialog does.
        expected_subset = {"mcp_ui", "platform_auth_session_dialog_vue"}
        missing = expected_subset - found
        assert not missing, (
            f"Vue-shipping fragment walker missed known fragments: {sorted(missing)}. "
            f"Found: {sorted(found)}"
        )


# -- The lint itself ----------------------------------------------------------


class TestThreeFrontendCoverage:
    """RFC-011 acceptance gate. A Vue-shipping fragment without sibling
    Svelte AND Flutter implementations (or an explicit
    ``frontend_skip_reason``) is a hard CI failure."""

    def test_every_vue_fragment_has_svelte_and_flutter_siblings(self) -> None:
        violations = _gather_violations()
        # Filter out fragments that opted out via the escape hatch.
        actionable = [v for v in violations if v[3] is None]
        if actionable:
            lines = ["Vue-shipping fragments missing Svelte/Flutter siblings:"]
            for area, vue_fragment, missing, _reason in actionable:
                base = _sibling_base_name(vue_fragment)
                missing_names = ", ".join(f"{base}_{suffix}" for suffix in missing)
                lines.append(
                    f"  - forge/features/{area}/templates/{vue_fragment}/ "
                    f"missing sibling(s): {missing_names}"
                )
            lines.append("")
            lines.append(
                "RFC-011 (Accepted, 1.2.0) requires every Vue-shipping fragment to "
                "ship sibling Svelte and Flutter implementations or set "
                "Fragment.frontend_skip_reason='<rationale>'. See "
                "docs/rfcs/RFC-011-frontend-api-client-survey.md."
            )
            pytest.fail("\n".join(lines))


# -- Escape-hatch wiring ------------------------------------------------------


class TestFrontendSkipReason:
    """Lock in that the ``Fragment.frontend_skip_reason`` field is
    present on the dataclass and defaults to ``None`` — so the escape
    hatch keeps working even if a future refactor renames neighbouring
    fields."""

    def test_field_default_is_none(self) -> None:
        # Pick any registered fragment; the default should hold for
        # every fragment that didn't explicitly set the field.
        sample = next(iter(FRAGMENT_REGISTRY.values()))
        assert sample.frontend_skip_reason is None, (
            f"Fragment {sample.name!r} unexpectedly has frontend_skip_reason="
            f"{sample.frontend_skip_reason!r}; the field should default to None."
        )

    def test_field_accepted_when_set(self) -> None:
        # Construct a Fragment directly with the marker set — this
        # asserts the dataclass accepts the kwarg (catches accidental
        # field removal at refactor time).
        from forge.config import BackendLanguage
        from forge.fragments import Fragment, FragmentImplSpec

        frag = Fragment(
            name="_three_frontend_coverage_sentinel",
            implementations={
                BackendLanguage.PYTHON: FragmentImplSpec(
                    fragment_dir="_fragments/__nonexistent_for_test__",
                ),
            },
            frontend_skip_reason="sentinel — only used by test_three_frontend_coverage",
        )
        assert frag.frontend_skip_reason is not None
        assert "sentinel" in frag.frontend_skip_reason
