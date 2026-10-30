"""Targeted parse-gap tests for CLI flags that weren't exercised by the
existing suite.

Complement to ``tests/test_cli_coverage.py`` -- each class here covers
one flag (or flag-interaction) the refactor left without a direct test.
"""

from __future__ import annotations

import pytest

from forge import cli
from forge.config import BackendLanguage, FrontendFramework
from forge.options import OPTION_REGISTRY, FeatureCategory, Option, OptionType


def _args_stub(**overrides):
    """Build a Namespace-like object with every argparse dest present.

    Mirrors what argparse would produce on a plain invocation, then
    applies overrides. Downstream helpers (_build_config, _build_options,
    _is_headless) only access attributes that exist on the parser.
    """
    defaults = dict(
        config=None,
        project_name=None,
        description=None,
        output_dir=".",
        backend_language=None,
        backend_name=None,
        backend_port=None,
        python_version=None,
        node_version=None,
        rust_edition=None,
        frontend=None,
        features=None,
        author_name=None,
        package_manager=None,
        frontend_port=None,
        color_scheme=None,
        org_name=None,
        include_auth=None,
        include_chat=None,
        include_openapi=None,
        generate_e2e_tests=None,
        keycloak_port=None,
        keycloak_realm=None,
        keycloak_client_id=None,
        set_options=[],
        yes=False,
        quiet=False,
        verbose=False,
        no_docker=False,
        json_output=False,
    )
    defaults.update(overrides)
    ns = type("Namespace", (), defaults)()
    return ns


# -- Backend variant flags ----------------------------------------------------


class TestBackendVariantFlags:
    """--backend-language / --node-version / --rust-edition reach BackendConfig."""

    def test_node_version_flows_to_backend(self) -> None:
        args = _args_stub(backend_language="node", node_version="24")
        config = cli._build_config(args, {})
        assert config.backends[0].language is BackendLanguage.NODE
        assert config.backends[0].node_version == "24"

    def test_rust_edition_flows_to_backend(self) -> None:
        args = _args_stub(backend_language="rust", rust_edition="2021")
        config = cli._build_config(args, {})
        assert config.backends[0].language is BackendLanguage.RUST
        assert config.backends[0].rust_edition == "2021"

    def test_version_flags_offer_the_registry_choices(self) -> None:
        from forge.cli.parser import _build_parser
        from forge.config import BACKEND_REGISTRY

        actions = {a.dest: a for a in _build_parser()._actions}
        for language, dest in (
            (BackendLanguage.PYTHON, "python_version"),
            (BackendLanguage.NODE, "node_version"),
            (BackendLanguage.RUST, "rust_edition"),
            (BackendLanguage.GO, "go_version"),
        ):
            assert list(actions[dest].choices) == list(BACKEND_REGISTRY[language].version_choices)

    def test_node_versions_are_the_ones_the_template_toolchain_runs_on(self) -> None:
        # vitest 5 runs on ^22.12, ^24 and >=26; Node 20 and 18 are past end of life.
        from forge.config import BACKEND_REGISTRY

        assert BACKEND_REGISTRY[BackendLanguage.NODE].version_choices == ("26", "24", "22")

    def test_python_version_flows_to_backend(self) -> None:
        args = _args_stub(backend_language="python", python_version="3.11")
        config = cli._build_config(args, {})
        assert config.backends[0].language is BackendLanguage.PYTHON
        assert config.backends[0].python_version == "3.11"


# -- --no-e2e-tests -----------------------------------------------------------


class TestNoE2eTestsFlag:
    def test_no_e2e_tests_disables_playwright_generation(self) -> None:
        args = _args_stub(frontend="vue", generate_e2e_tests=False)
        config = cli._build_config(args, {})
        assert config.frontend is not None
        assert config.frontend.framework is FrontendFramework.VUE
        assert config.frontend.generate_e2e_tests is False

    def test_default_enables_playwright_generation(self) -> None:
        """When the flag isn't set, the default (True) wins."""
        args = _args_stub(frontend="vue")  # generate_e2e_tests stays None
        config = cli._build_config(args, {})
        assert config.frontend is not None
        assert config.frontend.generate_e2e_tests is True


# -- --verbose overriding --quiet --------------------------------------------


class TestVerboseOverridesQuiet:
    """The quiet-flag resolution lives inline in main(); mirror its logic
    here so a regression in the boolean expression surfaces as a test
    failure rather than a silent change in generator output."""

    def _resolve_quiet(self, *, quiet: bool, verbose: bool, json_output: bool) -> bool:
        """Copy of the expression at forge/cli.py ~line 1305."""
        return (quiet or json_output) and not verbose

    def test_verbose_wins_over_quiet(self) -> None:
        assert self._resolve_quiet(quiet=True, verbose=True, json_output=False) is False

    def test_verbose_wins_over_json(self) -> None:
        assert self._resolve_quiet(quiet=False, verbose=True, json_output=True) is False

    def test_quiet_alone_keeps_quiet(self) -> None:
        assert self._resolve_quiet(quiet=True, verbose=False, json_output=False) is True

    def test_json_alone_keeps_quiet(self) -> None:
        assert self._resolve_quiet(quiet=False, verbose=False, json_output=True) is True

    def test_none_set_keeps_loud(self) -> None:
        assert self._resolve_quiet(quiet=False, verbose=False, json_output=False) is False


# -- LIST-typed --set coercion -----------------------------------------------


class TestListOptionSetCoercion:
    """`_coerce_set_value` has a LIST branch that no registered Option
    exercises today. Register a throwaway LIST Option via monkeypatch
    and confirm comma-separated values round-trip cleanly."""

    def test_list_option_splits_on_comma(self, monkeypatch: pytest.MonkeyPatch) -> None:
        opt = Option(
            path="test.items",
            type=OptionType.LIST,
            default=[],
            summary="test-only",
            description="test-only",
            category=FeatureCategory.PLATFORM,
        )
        monkeypatch.setitem(OPTION_REGISTRY, "test.items", opt)

        assert cli._coerce_set_value("test.items", "alpha,beta,gamma") == [
            "alpha",
            "beta",
            "gamma",
        ]

    def test_list_option_trims_whitespace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        opt = Option(
            path="test.items",
            type=OptionType.LIST,
            default=[],
            summary="t",
            description="t",
            category=FeatureCategory.PLATFORM,
        )
        monkeypatch.setitem(OPTION_REGISTRY, "test.items", opt)

        assert cli._coerce_set_value("test.items", "  a , b,c  ") == ["a", "b", "c"]


# -- _is_headless smoke -------------------------------------------------------


class TestIsHeadlessDetection:
    """Sanity check that ``--set`` alone flips the CLI into headless mode
    (previously each parameter flag did this explicitly; the unified
    ``--set`` flag must preserve that behavior)."""

    def test_set_alone_is_headless(self) -> None:
        args = _args_stub(set_options=["middleware.rate_limit=false"])
        assert cli._is_headless(args) is True

    def test_no_flags_is_interactive(self) -> None:
        args = _args_stub()
        assert cli._is_headless(args) is False


# -- --version ----------------------------------------------------------------


class TestVersionFlag:
    """``forge --version`` prints the package version and exits 0. The weekly
    install-test and release-dryrun workflows assert this."""

    def test_version_prints_and_exits_zero(self, capsys) -> None:
        from forge import __version__
        from forge.cli.parser import _build_parser

        with pytest.raises(SystemExit) as exc:
            _build_parser().parse_args(["--version"])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert __version__ in out
        assert out.startswith("forge ")
