"""Plugin SDK versioning tests (Epic 3 MVP).

Asserts:
  - ``SDK_VERSION`` parses cleanly and round-trips through
    ``_parse_sdk_version``.
  - ``_check_sdk_requirement`` evaluates each comparison operator.
  - ``ForgeAPI.require_sdk`` raises ``PluginError`` (code
    ``PLUGIN_SDK_INCOMPATIBLE``) when the host doesn't satisfy a
    plugin's declared range, and silently passes when it does.

Plugins target the SDK surface, not the forge package version. A bump
to :data:`forge.api.SDK_VERSION` requires a matching entry in
``docs/SDK_CHANGELOG.md`` — that file is also asserted to exist so the
release process doesn't drop it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forge.api import (
    SDK_VERSION,
    ForgeAPI,
    PluginRegistration,
    _check_sdk_requirement,
    _parse_sdk_version,
)
from forge.errors import PLUGIN_SDK_INCOMPATIBLE, PluginError


def test_sdk_version_parses() -> None:
    """The shipped constant must match the MAJOR.MINOR shape the parser
    accepts. Catches a malformed bump like ``"1.1.0"`` slipping in."""
    major, minor = _parse_sdk_version(SDK_VERSION)
    assert isinstance(major, int)
    assert isinstance(minor, int)
    assert major >= 1


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        (">=1.0", True),
        (">=1.1", True),
        (">=1.2", True),
        (">=1.4", True),
        (">=1.5", True),
        (">=1.6", True),
        (">=2.0", False),
        ("<2.0", True),
        ("<1.0", False),
        # SDK bumped to 1.6 (add_frontend_layout's ui_kits). The equality case
        # follows the live version so a future bump that forgets to update this
        # test fails loudly.
        ("==1.6", True),
        ("==1.5", False),
        ("==1.4", False),
        ("==1.3", False),
        ("==1.2", False),
        ("==2.0", False),
        (">=1.0, <2.0", True),
        (">=1.1, <2.0", True),
        (">=1.2, <2.0", True),
        (">=2.0, <3.0", False),
    ],
)
def test_sdk_requirement_eval(spec: str, expected: bool) -> None:
    """Every operator branch must evaluate against the live SDK_VERSION."""
    assert _check_sdk_requirement(spec) is expected


@pytest.mark.parametrize(
    "bad_spec",
    [
        "",
        "1.1",  # missing operator
        ">=",  # missing version
        ">=1",  # missing minor
        ">=1.1.0",  # patch component not supported
        ">=1.1pre",  # pre-release not supported
    ],
)
def test_sdk_requirement_rejects_bad_specs(bad_spec: str) -> None:
    """Malformed requirement strings raise ``ValueError`` so plugin
    authors get an immediate parse error rather than a silent False."""
    with pytest.raises(ValueError):
        _check_sdk_requirement(bad_spec)


def _api_for(name: str = "test_plugin") -> ForgeAPI:
    return ForgeAPI(PluginRegistration(name=name, module="test"))


def test_require_sdk_passes_for_compatible_host() -> None:
    """A plugin asking for the current SDK or any wider range loads OK."""
    api = _api_for()
    api.require_sdk(f">={SDK_VERSION}")
    api.require_sdk(">=1.0")  # historical floor


def test_require_sdk_raises_plugin_error_when_incompatible() -> None:
    """A plugin asking for a future SDK fails fast with PLUGIN_SDK_INCOMPATIBLE."""
    api = _api_for(name="from_the_future")
    with pytest.raises(PluginError) as excinfo:
        api.require_sdk(">=99.0")
    assert excinfo.value.code == PLUGIN_SDK_INCOMPATIBLE
    assert "from_the_future" in str(excinfo.value)


def test_require_sdk_raises_plugin_error_for_bad_spec() -> None:
    """Malformed spec from the plugin surfaces as PLUGIN_SDK_INCOMPATIBLE
    rather than a raw ValueError, so the error envelope stays
    plugin-coded."""
    api = _api_for(name="bad_spec_plugin")
    with pytest.raises(PluginError) as excinfo:
        api.require_sdk("garbage")
    assert excinfo.value.code == PLUGIN_SDK_INCOMPATIBLE


def test_sdk_changelog_exists() -> None:
    """``docs/SDK_CHANGELOG.md`` is the source of truth for SDK bumps —
    it must exist so release engineering can rely on it. PRs that mutate
    ``forge.api.__all__`` are expected to add an entry; this test only
    enforces the file is present."""
    changelog = Path(__file__).resolve().parent.parent / "docs" / "SDK_CHANGELOG.md"
    assert changelog.is_file(), (
        f"docs/SDK_CHANGELOG.md is missing at {changelog}; the SDK "
        "version contract relies on it being present"
    )
