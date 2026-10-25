"""Tests for the unified Option registry, JSON-Schema emitter, and CLI
surface that ride on top of it.

Sister registry-level invariant suites (kept in their own files so the
collection here stays focused on Options, not "every cross-cutting
invariant in one giant module"):

- ``tests/test_fragment_parity.py`` — RFC-006 cross-backend parity
  tiers asserted against ``FRAGMENT_REGISTRY``.
- ``tests/test_three_frontend_coverage.py`` — RFC-011 (Accepted, 1.2.0)
  three-frontend coverage lint: any fragment shipping a Vue composable
  must have sibling Svelte AND Flutter implementations or an explicit
  ``Fragment.frontend_skip_reason`` marker.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from forge.capability_resolver import resolve
from forge.config import BackendConfig, BackendLanguage, ProjectConfig
from forge.options import (
    CATEGORY_ORDER,
    OPTION_REGISTRY,
    FeatureCategory,
    Option,
    OptionType,
    ordered_options,
    to_json_schema,
)

# -- Registry shape -----------------------------------------------------------


class TestRegistryInvariants:
    def test_registry_non_empty(self) -> None:
        assert OPTION_REGISTRY, "OPTION_REGISTRY must not be empty"

    def test_every_path_is_dotted_or_valid_identifier(self) -> None:
        import re

        pattern = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*$")
        for path in OPTION_REGISTRY:
            assert pattern.fullmatch(path), f"Bad option path: {path!r}"

    def test_every_option_has_summary_and_description(self) -> None:
        for path, opt in OPTION_REGISTRY.items():
            assert opt.summary.strip(), f"{path}: empty summary"
            assert opt.description.strip(), f"{path}: empty description"

    def test_every_category_used_is_registered(self) -> None:
        known = set(CATEGORY_ORDER)
        for opt in OPTION_REGISTRY.values():
            assert opt.category in known, f"{opt.path}: unknown category {opt.category}"

    def test_every_enum_option_has_non_empty_options(self) -> None:
        for opt in OPTION_REGISTRY.values():
            if opt.type is OptionType.ENUM:
                assert opt.options, f"{opt.path}: ENUM must have options"
                assert opt.default in opt.options, f"{opt.path}: default not in options"

    def test_every_bool_option_default_is_bool(self) -> None:
        for opt in OPTION_REGISTRY.values():
            if opt.type is OptionType.BOOL:
                assert isinstance(opt.default, bool)

    def test_enables_keys_match_option_shape(self) -> None:
        for opt in OPTION_REGISTRY.values():
            if opt.type is OptionType.BOOL:
                for key in opt.enables:
                    assert key in (True, False), f"{opt.path}: bool key {key!r}"
            elif opt.type is OptionType.ENUM:
                for key in opt.enables:
                    assert key in opt.options, f"{opt.path}: enables key {key!r} not in options"

    def test_fragment_references_all_exist(self) -> None:
        """Every fragment key referenced by an Option must be registered."""
        from forge.fragments import FRAGMENT_REGISTRY

        for opt in OPTION_REGISTRY.values():
            for _value, fragments in opt.enables.items():
                for fkey in fragments:
                    assert fkey in FRAGMENT_REGISTRY, (
                        f"Option {opt.path}: enables unknown fragment {fkey!r}"
                    )


# -- Option.validate_value ----------------------------------------------------


class TestValidateValue:
    def test_bool_accepts_true_false(self) -> None:
        opt = Option(
            path="x.on",
            type=OptionType.BOOL,
            default=False,
            summary="s",
            description="d",
            category=FeatureCategory.PLATFORM,
        )
        opt.validate_value(True)
        opt.validate_value(False)

    def test_bool_rejects_string(self) -> None:
        opt = Option(
            path="x.on",
            type=OptionType.BOOL,
            default=False,
            summary="s",
            description="d",
            category=FeatureCategory.PLATFORM,
        )
        with pytest.raises(ValueError, match="bool"):
            opt.validate_value("true")

    def test_enum_rejects_unknown_value(self) -> None:
        opt = Option(
            path="x.pick",
            type=OptionType.ENUM,
            default="a",
            options=("a", "b"),
            summary="s",
            description="d",
            category=FeatureCategory.PLATFORM,
        )
        with pytest.raises(ValueError, match="invalid value"):
            opt.validate_value("c")

    def test_int_bounds_enforced(self) -> None:
        opt = Option(
            path="x.k",
            type=OptionType.INT,
            default=5,
            min=1,
            max=10,
            summary="s",
            description="d",
            category=FeatureCategory.PLATFORM,
        )
        opt.validate_value(1)
        opt.validate_value(10)
        with pytest.raises(ValueError, match="min"):
            opt.validate_value(0)
        with pytest.raises(ValueError, match="max"):
            opt.validate_value(11)

    def test_str_pattern_enforced(self) -> None:
        opt = Option(
            path="x.s",
            type=OptionType.STR,
            default="abc",
            pattern=r"[a-z]+",
            summary="s",
            description="d",
            category=FeatureCategory.PLATFORM,
        )
        opt.validate_value("hello")
        with pytest.raises(ValueError, match="pattern"):
            opt.validate_value("HELLO")


class TestOptionCompatMetadata:
    """Initiative #7 — structural checks on the new compatibility fields."""

    def test_compat_defaults_match_legacy_behaviour(self) -> None:
        """Default compat metadata: backend-required, no DB requirement,
        any built-in backend / frontend, no incompatibilities. This mirrors
        the pre-Initiative-#7 state where the resolver had no per-Option
        compatibility metadata at all."""
        opt = Option(
            path="x.compat",
            type=OptionType.BOOL,
            default=False,
            summary="s",
            description="d",
            category=FeatureCategory.PLATFORM,
        )
        assert opt.requires_database is False
        assert opt.requires_backend is True
        assert opt.allowed_backends is None
        assert opt.allowed_frontends is None
        assert opt.incompatible_with == ()

    def test_allowed_backends_empty_tuple_rejected(self) -> None:
        from forge.config._backend import BackendLanguage  # noqa: PLC0415

        with pytest.raises(ValueError, match="allowed_backends must be None"):
            Option(
                path="x.compat",
                type=OptionType.BOOL,
                default=False,
                summary="s",
                description="d",
                category=FeatureCategory.PLATFORM,
                allowed_backends=(),
            )
        # Sanity: non-empty tuple is accepted.
        Option(
            path="x.compat2",
            type=OptionType.BOOL,
            default=False,
            summary="s",
            description="d",
            category=FeatureCategory.PLATFORM,
            allowed_backends=(BackendLanguage.PYTHON,),
        )

    def test_allowed_frontends_empty_tuple_rejected(self) -> None:
        from forge.config._frontend import FrontendFramework  # noqa: PLC0415

        with pytest.raises(ValueError, match="allowed_frontends must be None"):
            Option(
                path="x.compat",
                type=OptionType.BOOL,
                default=False,
                summary="s",
                description="d",
                category=FeatureCategory.PLATFORM,
                allowed_frontends=(),
            )
        # Sanity: non-empty tuple accepted.
        Option(
            path="x.compat2",
            type=OptionType.BOOL,
            default=False,
            summary="s",
            description="d",
            category=FeatureCategory.PLATFORM,
            allowed_frontends=(FrontendFramework.VUE,),
        )

    def test_incompatible_with_self_referential_rejected(self) -> None:
        with pytest.raises(ValueError, match="can't conflict with itself"):
            Option(
                path="x.self",
                type=OptionType.BOOL,
                default=False,
                summary="s",
                description="d",
                category=FeatureCategory.PLATFORM,
                incompatible_with=("x.self",),
            )

    def test_incompatible_with_duplicates_rejected(self) -> None:
        with pytest.raises(ValueError, match="duplicate entries in incompatible_with"):
            Option(
                path="x.dup",
                type=OptionType.BOOL,
                default=False,
                summary="s",
                description="d",
                category=FeatureCategory.PLATFORM,
                incompatible_with=("a.b", "a.b"),
            )

    def test_incompatible_with_invalid_path_rejected(self) -> None:
        with pytest.raises(ValueError, match="Invalid option path"):
            Option(
                path="x.bad_target",
                type=OptionType.BOOL,
                default=False,
                summary="s",
                description="d",
                category=FeatureCategory.PLATFORM,
                incompatible_with=("not a path",),
            )

    def test_requires_database_accepted(self) -> None:
        opt = Option(
            path="x.needs_db",
            type=OptionType.BOOL,
            default=False,
            summary="s",
            description="d",
            category=FeatureCategory.PLATFORM,
            requires_database=True,
        )
        assert opt.requires_database is True

    def test_requires_backend_false_accepted(self) -> None:
        """Frontend-only options opt out of the default backend requirement."""
        opt = Option(
            path="x.frontend_only",
            type=OptionType.BOOL,
            default=False,
            summary="s",
            description="d",
            category=FeatureCategory.PLATFORM,
            requires_backend=False,
        )
        assert opt.requires_backend is False


class TestIsActiveValue:
    """``Option.is_active_value`` powers the compatibility walker — it
    decides whether a registered option's value should trigger
    metadata checks. BOOL active iff True; ENUM active iff the value
    resolves to at least one fragment; other types inactive."""

    def test_bool_true_active(self) -> None:
        opt = Option(
            path="x.on",
            type=OptionType.BOOL,
            default=False,
            summary="s",
            description="d",
            category=FeatureCategory.PLATFORM,
        )
        assert opt.is_active_value(True) is True
        assert opt.is_active_value(False) is False

    def test_enum_active_when_value_enables_fragments(self) -> None:
        opt = Option(
            path="x.pick",
            type=OptionType.ENUM,
            default="none",
            options=("none", "a", "b"),
            summary="s",
            description="d",
            category=FeatureCategory.PLATFORM,
            enables={"a": ("frag_a",), "b": ("frag_b",)},
        )
        assert opt.is_active_value("a") is True
        assert opt.is_active_value("b") is True
        # ``none`` doesn't map to fragments → considered inactive.
        assert opt.is_active_value("none") is False

    def test_str_and_int_inactive(self) -> None:
        opt_str = Option(
            path="x.s",
            type=OptionType.STR,
            default="hello",
            summary="s",
            description="d",
            category=FeatureCategory.PLATFORM,
        )
        opt_int = Option(
            path="x.k",
            type=OptionType.INT,
            default=5,
            summary="s",
            description="d",
            category=FeatureCategory.PLATFORM,
        )
        assert opt_str.is_active_value("hello") is False
        assert opt_int.is_active_value(5) is False


class TestOptionConstruction:
    def test_invalid_path_rejected(self) -> None:
        with pytest.raises(ValueError, match="Invalid option path"):
            Option(
                path="bad path",
                type=OptionType.BOOL,
                default=False,
                summary="s",
                description="d",
                category=FeatureCategory.PLATFORM,
            )

    def test_bool_default_wrong_type_rejected(self) -> None:
        with pytest.raises(ValueError, match="BOOL default"):
            Option(
                path="x.y",
                type=OptionType.BOOL,
                default="not-bool",
                summary="s",
                description="d",
                category=FeatureCategory.PLATFORM,
            )

    def test_enum_without_options_rejected(self) -> None:
        with pytest.raises(ValueError, match="ENUM requires"):
            Option(
                path="x.y",
                type=OptionType.ENUM,
                default="a",
                summary="s",
                description="d",
                category=FeatureCategory.PLATFORM,
            )

    def test_min_on_non_int_rejected(self) -> None:
        with pytest.raises(ValueError, match="min.*INT"):
            Option(
                path="x.s",
                type=OptionType.STR,
                default="abc",
                min=1,
                summary="s",
                description="d",
                category=FeatureCategory.PLATFORM,
            )


# -- Resolver semantics (using the real registry) -----------------------------


class TestResolverAgainstRealRegistry:
    def _project(self, options: dict[str, object]) -> ProjectConfig:
        return ProjectConfig(
            project_name="p",
            backends=[
                BackendConfig(
                    name="api",
                    project_name="p",
                    language=BackendLanguage.PYTHON,
                ),
            ],
            options=options,
        )

    def test_default_always_on_option_produces_correlation_id(self) -> None:
        plan = resolve(self._project({}))
        names = {rf.fragment.name for rf in plan.ordered}
        assert "correlation_id" in names

    def test_rag_backend_qdrant_pulls_rag_stack(self) -> None:
        # 1.0.0a2: rag.backend now uses the port+adapter pattern (ADR-002).
        plan = resolve(self._project({"rag.backend": "qdrant"}))
        names = {rf.fragment.name for rf in plan.ordered}
        assert {
            "rag_pipeline",
            "conversation_persistence",
            "vector_store_port",
            "vector_store_qdrant",
        }.issubset(names)

    def test_rag_backend_none_includes_no_rag_fragments(self) -> None:
        plan = resolve(self._project({"rag.backend": "none"}))
        names = {rf.fragment.name for rf in plan.ordered}
        assert not any(n.startswith("rag_") for n in names)
        assert not any(n.startswith("vector_store_") for n in names)

    def test_unknown_option_path_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown option"):
            self._project({"not.a.real.option": True}).validate()

    def test_option_values_carries_defaults(self) -> None:
        plan = resolve(self._project({"middleware.rate_limit": False}))
        # User value persists, other options populated with defaults.
        assert plan.option_values["middleware.rate_limit"] is False
        assert plan.option_values["rag.backend"] == "none"


# -- JSON Schema emitter ------------------------------------------------------


class TestJsonSchemaEmitter:
    def test_schema_is_draft_2020_12(self) -> None:
        schema = to_json_schema()
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"

    def test_every_registered_option_appears_in_schema(self) -> None:
        schema = to_json_schema()
        for path in OPTION_REGISTRY:
            assert path in schema["properties"], f"missing from schema: {path}"

    def test_bool_options_rendered_as_boolean(self) -> None:
        schema = to_json_schema()
        prop = schema["properties"]["middleware.rate_limit"]
        assert prop["type"] == "boolean"

    def test_enum_options_carry_enum_list(self) -> None:
        schema = to_json_schema()
        prop = schema["properties"]["rag.backend"]
        assert prop["type"] == "string"
        assert "qdrant" in prop["enum"]

    def test_int_options_carry_bounds(self) -> None:
        schema = to_json_schema()
        prop = schema["properties"]["rag.top_k"]
        assert prop["type"] == "integer"
        assert prop["minimum"] == 1
        assert prop["maximum"] == 100

    def test_schema_is_valid_json_schema(self) -> None:
        """Self-check: the emitted schema must itself validate against
        Draft 2020-12 meta-schema."""
        try:
            import jsonschema  # noqa: PLC0415
        except ImportError:
            pytest.skip("jsonschema not installed")

        schema = to_json_schema()
        jsonschema.Draft202012Validator.check_schema(schema)


# -- ordered_options sequence -------------------------------------------------


class TestOrderedOptions:
    def test_ordered_walks_categories_in_registry_order(self) -> None:
        seen_categories: list[FeatureCategory] = []
        for opt in ordered_options():
            if not seen_categories or seen_categories[-1] != opt.category:
                seen_categories.append(opt.category)
        # Categories appear in the CATEGORY_ORDER sequence (no skipping
        # back), though some categories may be absent if they're empty.
        indices = [CATEGORY_ORDER.index(c) for c in seen_categories]
        assert indices == sorted(indices)


# -- CLI integration ----------------------------------------------------------


class TestCliBuildOptions:
    def test_dotted_yaml_keys_flattened(self) -> None:
        from forge.cli import _build_options

        class _Args:
            set_options: list[str] = []

        cfg = {"options": {"middleware.rate_limit": False, "rag.backend": "qdrant"}}
        opts = _build_options(_Args(), cfg)
        assert opts == {"middleware.rate_limit": False, "rag.backend": "qdrant"}

    def test_nested_yaml_flattened_to_dotted(self) -> None:
        from forge.cli import _build_options

        class _Args:
            set_options: list[str] = []

        cfg = {
            "options": {
                "middleware": {"rate_limit": False},
                "rag": {"backend": "qdrant", "top_k": 10},
            }
        }
        opts = _build_options(_Args(), cfg)
        assert opts == {
            "middleware.rate_limit": False,
            "rag.backend": "qdrant",
            "rag.top_k": 10,
        }

    def test_set_flag_overrides_yaml(self) -> None:
        from forge.cli import _build_options

        class _Args:
            set_options = ["middleware.rate_limit=true"]

        cfg = {"options": {"middleware.rate_limit": False}}
        opts = _build_options(_Args(), cfg)
        assert opts["middleware.rate_limit"] is True

    def test_set_coerces_bool_and_int(self) -> None:
        from forge.cli import _build_options

        class _Args:
            set_options = [
                "middleware.rate_limit=false",
                "rag.backend=qdrant",
                "rag.top_k=7",
            ]

        opts = _build_options(_Args(), {})
        assert opts["middleware.rate_limit"] is False
        assert opts["rag.backend"] == "qdrant"
        assert opts["rag.top_k"] == 7

    def test_set_without_equals_raises(self) -> None:
        from forge.cli import _build_options

        class _Args:
            set_options = ["no-equals-here"]

        with pytest.raises(ValueError, match="PATH=VALUE"):
            _build_options(_Args(), {})


class TestCliListDispatch:
    def test_json_format_valid(self, capsys: pytest.CaptureFixture[str]) -> None:
        from forge.cli import _dispatch_list

        with pytest.raises(SystemExit) as excinfo:
            _dispatch_list("json")
        assert excinfo.value.code == 0
        out = capsys.readouterr().out
        rows = json.loads(out)
        assert isinstance(rows, list)
        names = {r["name"] for r in rows}
        assert "middleware.rate_limit" in names
        assert "rag.backend" in names

    def test_yaml_format_valid(self, capsys: pytest.CaptureFixture[str]) -> None:
        from forge.cli import _dispatch_list

        with pytest.raises(SystemExit) as excinfo:
            _dispatch_list("yaml")
        assert excinfo.value.code == 0
        out = capsys.readouterr().out
        rows = yaml.safe_load(out)
        assert isinstance(rows, list)
        names = {r["name"] for r in rows}
        assert "rag.backend" in names

    def test_text_format_two_columns(self, capsys: pytest.CaptureFixture[str]) -> None:
        from forge.cli import _dispatch_list

        with pytest.raises(SystemExit):
            _dispatch_list("text")
        out = capsys.readouterr().out
        assert out.splitlines()[0].startswith("NAME")
        # At least one known path shows up in the body.
        assert "rag.backend" in out


class TestCliSchemaDispatch:
    def test_schema_prints_valid_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        from forge.cli import _dispatch_schema

        with pytest.raises(SystemExit) as excinfo:
            _dispatch_schema()
        assert excinfo.value.code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert "rag.backend" in payload["properties"]


class TestCliDescribe:
    def test_known_path_prints_description(self, capsys: pytest.CaptureFixture[str]) -> None:
        from forge.cli import _describe_option

        with pytest.raises(SystemExit) as excinfo:
            _describe_option("rag.backend")
        assert excinfo.value.code == 0
        out = capsys.readouterr().out
        assert "rag.backend" in out
        assert "enum" in out.lower()
        assert "qdrant" in out

    def test_unknown_path_exits_nonzero_with_suggestion(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from forge.cli import _describe_option

        with pytest.raises(SystemExit) as excinfo:
            _describe_option("rag.backends")  # typo: trailing s
        assert excinfo.value.code == 1
        err = capsys.readouterr().err
        assert "Unknown option" in err
        assert "rag.backend" in err  # close-match suggestion


# -- Integration: YAML → ProjectConfig → resolve ------------------------------


class TestYamlRoundTrip:
    def test_full_stack_yaml_resolves(self, tmp_path: Path) -> None:
        """A realistic stack.yaml with options: should produce a working plan."""
        from forge.cli import _build_config, _load_config_file

        cfg_path = tmp_path / "stack.yaml"
        cfg_path.write_text(
            "\n".join(
                [
                    "project_name: my_platform",
                    "backends:",
                    "  - name: backend",
                    "    language: python",
                    "options:",
                    "  middleware.rate_limit: false",
                    "  rag.backend: qdrant",
                    "  rag.top_k: 10",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        class _Args:
            config: str = str(cfg_path)
            project_name = None
            description = None
            output_dir = str(tmp_path)
            backend_language = None
            backend_name = None
            backend_port = None
            python_version = None
            node_version = None
            rust_edition = None
            frontend = None
            features = None
            author_name = None
            package_manager = None
            frontend_port = None
            color_scheme = None
            org_name = None
            include_auth = None
            include_chat = None
            include_openapi = None
            generate_e2e_tests = None
            keycloak_port = None
            keycloak_realm = None
            keycloak_client_id = None
            set_options: list[str] = []

        cfg = _load_config_file(str(cfg_path))
        config = _build_config(_Args(), cfg)
        config.validate()
        plan = resolve(config)
        names = {rf.fragment.name for rf in plan.ordered}
        # 1.0.0a2: rag.backend=qdrant now enables the port+adapter pair.
        assert "vector_store_qdrant" in names
        assert "vector_store_port" in names
        assert plan.option_values["rag.top_k"] == 10


# -- Fragment materialization -------------------------------------------------
#
# One parametric case per registered Option, derived from OPTION_REGISTRY
# itself. For BOOL Options we check default=False toggled on via explicit
# True; for ENUM Options we walk every value. The multi-backend project
# means fragment-filtering branches execute for all three language
# registrations, and any Option whose enables map is silently broken
# shows up here instead of hiding behind registry-shape invariants.


def _multi_backend_project(options: dict[str, object] | None = None) -> ProjectConfig:
    """Project with python + node + rust + go backends so fragment-per-backend
    resolution exercises every language branch."""
    options = options or {}
    # Platform-auth requires Keycloak (the gatekeeper stack depends on the
    # keycloak+redis services); the resolver coerces auth.mode→none when keycloak
    # is off. Enable keycloak whenever the config needs auth — an explicit
    # auth.mode=generate, or an MCP surface (platform.mcp / agent.mode=
    # tool_calling) which the security guard requires auth for — so the
    # platform-auth fanout stays effective and the MCP guard is satisfied.
    needs_auth = (
        options.get("auth.mode") == "generate"
        # auth.provider is a sub-discriminator of auth.mode=generate; selecting
        # a real provider implies the auth stack is generated (and thus needs
        # keycloak so the resolver doesn't coerce auth.mode→none, which would
        # in turn gate the provider off).
        or options.get("auth.provider") in ("gatekeeper", "in_memory", "oidc_generic")
        or options.get("platform.mcp") is True
        or options.get("agent.mode") == "tool_calling"
    )
    include_keycloak = needs_auth
    return ProjectConfig(
        project_name="p",
        backends=[
            BackendConfig(
                name="api-py", project_name="p", language=BackendLanguage.PYTHON, server_port=5001
            ),
            BackendConfig(
                name="api-js", project_name="p", language=BackendLanguage.NODE, server_port=5002
            ),
            BackendConfig(
                name="api-rs", project_name="p", language=BackendLanguage.RUST, server_port=5003
            ),
            BackendConfig(
                name="api-go", project_name="p", language=BackendLanguage.GO, server_port=5004
            ),
        ],
        options=options,
        include_keycloak=include_keycloak,
    )


def _bool_materialization_cases() -> list[tuple[str, tuple[str, ...]]]:
    """(path, expected_fragments) for every BOOL Option that enables fragments.

    Skips BOOL Options whose ``True`` key is absent from ``enables`` (none
    today, but harmless if one appears later).
    """
    cases: list[tuple[str, tuple[str, ...]]] = []
    for path, opt in OPTION_REGISTRY.items():
        if opt.type is not OptionType.BOOL:
            continue
        expected = opt.enables.get(True, ())
        if not expected:
            continue
        cases.append((path, expected))
    return cases


# Values a project with Go services rejects because Go has no implementation
# (its services would run without tenant isolation); materialized without Go.
_REJECTED_WITH_GO: frozenset[tuple[str, object]] = frozenset(
    {("database.multitenancy", "schema_per_tenant")}
)


def _enum_materialization_cases() -> list[tuple[str, object, tuple[str, ...]]]:
    """(path, value, expected_fragments) for every ENUM value with fragments."""
    cases: list[tuple[str, object, tuple[str, ...]]] = []
    for path, opt in OPTION_REGISTRY.items():
        if opt.type is not OptionType.ENUM:
            continue
        for value in opt.options:
            expected = opt.enables.get(value, ())
            if not expected:
                continue
            cases.append((path, value, expected))
    return cases


class TestFragmentMaterialization:
    """Every Option's ``enables`` entry resolves to the declared fragments."""

    @pytest.mark.parametrize(
        "path,expected",
        _bool_materialization_cases(),
        ids=lambda v: v if isinstance(v, str) else "",
    )
    def test_bool_option_enables_expected_fragments(
        self, path: str, expected: tuple[str, ...]
    ) -> None:
        plan = resolve(_multi_backend_project({path: True}))
        names = {rf.fragment.name for rf in plan.ordered}
        missing = [f for f in expected if f not in names]
        assert not missing, f"Setting {path}=True should enable {expected}; missing: {missing}"

    @pytest.mark.parametrize(
        "path,value,expected",
        _enum_materialization_cases(),
        ids=lambda v: v if isinstance(v, str) else repr(v),
    )
    def test_enum_option_value_enables_expected_fragments(
        self, path: str, value: object, expected: tuple[str, ...]
    ) -> None:
        config = _multi_backend_project({path: value})
        if (path, value) in _REJECTED_WITH_GO:
            config.backends = [b for b in config.backends if b.language is not BackendLanguage.GO]
        plan = resolve(config)
        names = {rf.fragment.name for rf in plan.ordered}
        missing = [f for f in expected if f not in names]
        assert not missing, f"Setting {path}={value!r} should enable {expected}; missing: {missing}"

    def test_bool_option_set_false_does_not_enable(self) -> None:
        """A BOOL Option with default=True, set explicitly to False, must
        NOT pull in its fragments. Guards against a resolver bug where the
        user's False silently loses to the default True."""
        # rate_limit defaults to True and enables the 'rate_limit' fragment.
        plan = resolve(_multi_backend_project({"middleware.rate_limit": False}))
        names = {rf.fragment.name for rf in plan.ordered}
        assert "rate_limit" not in names
