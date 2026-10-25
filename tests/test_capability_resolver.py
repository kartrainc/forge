"""Tests for capability_resolver: defaults, topo sort, conflicts, backend filtering."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest

from forge.capability_resolver import resolve
from forge.config import BackendConfig, BackendLanguage, ProjectConfig
from forge.errors import GeneratorError, OptionsError
from forge.fragments import Fragment, FragmentImplSpec
from forge.options import FeatureCategory, Option, OptionType


def _project(
    langs: list[BackendLanguage],
    options: dict[str, object] | None = None,
    option_origins: dict[str, str] | None = None,
) -> ProjectConfig:
    backends = [
        BackendConfig(name=f"svc-{i}", project_name="P", language=lang, server_port=5000 + i)
        for i, lang in enumerate(langs)
    ]
    return ProjectConfig(
        project_name="P",
        backends=backends,
        frontends=[],
        options=options or {},
        option_origins=option_origins or {},
    )


def _mk_fragment(name: str, **kw) -> Fragment:
    defaults = dict(
        name=name,
        implementations={BackendLanguage.PYTHON: FragmentImplSpec(fragment_dir=f"{name}/python")},
    )
    defaults.update(kw)
    return Fragment(**defaults)


def _mk_option(path: str, *, fragments: tuple[str, ...], default: bool = False) -> Option:
    return Option(
        path=path,
        type=OptionType.BOOL,
        default=default,
        summary=path,
        description=path,
        category=FeatureCategory.PLATFORM,
        enables={True: fragments},
    )


@pytest.fixture
def isolated_registries() -> Iterator[tuple[dict, dict]]:
    """Swap both OPTION_REGISTRY and FRAGMENT_REGISTRY for empty dicts.

    Tests can register their own fakes without touching the real
    catalogue. Patched at every import site so the resolver sees the
    same empty dict the test populated.
    """
    options: dict = {}
    fragments: dict = {}
    with (
        patch("forge.capability_resolver.OPTION_REGISTRY", options),
        patch("forge.options.OPTION_REGISTRY", options),
        patch("forge.capability_resolver.FRAGMENT_REGISTRY", fragments),
        patch("forge.fragments.FRAGMENT_REGISTRY", fragments),
        patch("forge.config.OPTION_REGISTRY", options, create=True),
    ):
        yield options, fragments


class TestDefaults:
    def test_default_true_option_enables_fragment_without_user_input(
        self, isolated_registries
    ) -> None:
        options, fragments = isolated_registries
        fragments["corr"] = _mk_fragment("corr")
        options["corr.always_on"] = _mk_option("corr.always_on", fragments=("corr",), default=True)
        plan = resolve(_project([BackendLanguage.PYTHON]))
        assert [rf.fragment.name for rf in plan.ordered] == ["corr"]

    def test_default_false_option_stays_off(self, isolated_registries) -> None:
        options, fragments = isolated_registries
        fragments["off"] = _mk_fragment("off")
        options["off.toggle"] = _mk_option("off.toggle", fragments=("off",), default=False)
        plan = resolve(_project([BackendLanguage.PYTHON]))
        assert plan.ordered == ()

    def test_user_set_true_enables_fragment(self, isolated_registries) -> None:
        options, fragments = isolated_registries
        fragments["x"] = _mk_fragment("x")
        options["x.on"] = _mk_option("x.on", fragments=("x",), default=False)
        plan = resolve(_project([BackendLanguage.PYTHON], {"x.on": True}))
        assert [rf.fragment.name for rf in plan.ordered] == ["x"]


class TestTopoSort:
    def test_dependency_ordered_before_dependent(self, isolated_registries) -> None:
        options, fragments = isolated_registries
        fragments["base"] = _mk_fragment("base")
        fragments["built_on_base"] = _mk_fragment("built_on_base", depends_on=("base",))
        options["a.base"] = _mk_option("a.base", fragments=("base",), default=True)
        options["a.built"] = _mk_option("a.built", fragments=("built_on_base",), default=True)
        plan = resolve(_project([BackendLanguage.PYTHON]))
        names = [rf.fragment.name for rf in plan.ordered]
        assert names.index("base") < names.index("built_on_base")

    def test_transitive_dependency_auto_included(self, isolated_registries) -> None:
        """A fragment's depends_on is auto-pulled; user doesn't opt in explicitly."""
        options, fragments = isolated_registries
        fragments["base"] = _mk_fragment("base")
        fragments["dependent"] = _mk_fragment("dependent", depends_on=("base",))
        options["a.on"] = _mk_option("a.on", fragments=("dependent",), default=True)
        plan = resolve(_project([BackendLanguage.PYTHON]))
        names = [rf.fragment.name for rf in plan.ordered]
        assert "base" in names
        assert "dependent" in names
        assert names.index("base") < names.index("dependent")

    def test_cycle_detected(self, isolated_registries) -> None:
        options, fragments = isolated_registries
        fragments["a"] = _mk_fragment("a", depends_on=("b",))
        fragments["b"] = _mk_fragment("b", depends_on=("a",))
        options["t.on"] = _mk_option("t.on", fragments=("a", "b"), default=True)
        with pytest.raises(GeneratorError, match="[Cc]yclic"):
            resolve(_project([BackendLanguage.PYTHON]))


class TestConflicts:
    def test_two_conflicting_raises(self, isolated_registries) -> None:
        options, fragments = isolated_registries
        fragments["x"] = _mk_fragment("x", conflicts_with=("y",))
        fragments["y"] = _mk_fragment("y", conflicts_with=("x",))
        options["a.on"] = _mk_option("a.on", fragments=("x", "y"), default=True)
        with pytest.raises(GeneratorError, match="conflict"):
            resolve(_project([BackendLanguage.PYTHON]))


class TestCapabilities:
    def test_capabilities_deduped(self, isolated_registries) -> None:
        options, fragments = isolated_registries
        fragments["a"] = _mk_fragment("a", capabilities=("redis",))
        fragments["b"] = _mk_fragment("b", capabilities=("redis", "postgres-pgvector"))
        options["p.a"] = _mk_option("p.a", fragments=("a",), default=True)
        options["p.b"] = _mk_option("p.b", fragments=("b",), default=True)
        plan = resolve(_project([BackendLanguage.PYTHON]))
        assert plan.capabilities == frozenset({"redis", "postgres-pgvector"})


class TestBackendCompatibility:
    def test_unsupported_backend_raises_when_user_requested(self, isolated_registries) -> None:
        options, fragments = isolated_registries
        fragments["rust_only"] = _mk_fragment(
            "rust_only",
            implementations={BackendLanguage.RUST: FragmentImplSpec(fragment_dir="r")},
        )
        options["p.on"] = _mk_option("p.on", fragments=("rust_only",), default=False)
        with pytest.raises(GeneratorError, match="supported backends"):
            resolve(_project([BackendLanguage.PYTHON], {"p.on": True}))

    def test_default_with_no_matching_backend_skips_silently(self, isolated_registries) -> None:
        """A Python-only fragment selected by a default value must skip on a Rust project."""
        options, fragments = isolated_registries
        fragments["py_only"] = _mk_fragment(
            "py_only",
            implementations={BackendLanguage.PYTHON: FragmentImplSpec(fragment_dir="p")},
        )
        options["p.on"] = _mk_option("p.on", fragments=("py_only",), default=True)
        plan = resolve(_project([BackendLanguage.RUST]))
        assert plan.ordered == ()

    def test_unknown_option_path_raises(self, isolated_registries) -> None:
        """Unknown paths in config.options are caught by the resolver."""
        # Fixture keeps OPTION_REGISTRY empty — any path is "unknown".
        _ = isolated_registries
        with pytest.raises(GeneratorError, match="Unknown option"):
            resolve(_project([BackendLanguage.PYTHON], {"nope.nada": True}))

    def test_discriminator_fanout_skips_unmatched_languages_silently(
        self, isolated_registries
    ) -> None:
        """A discriminator option that fans out per-language fragments must NOT
        hard-error when only some of them have a compatible backend.

        Real-world example: ``auth.mode=generate`` enables
        ``platform_auth_sdk_python`` + ``_node`` + ``_rust``. A Python-only
        project should silently skip the Node and Rust SDK fragments,
        not raise "supported backends … not present".

        Single-fragment options preserve the hard-error behavior — that's
        a real user typo, tested above by
        ``test_unsupported_backend_raises_when_user_requested``.
        """
        options, fragments = isolated_registries
        fragments["sdk_python"] = _mk_fragment(
            "sdk_python",
            implementations={BackendLanguage.PYTHON: FragmentImplSpec(fragment_dir="p")},
        )
        fragments["sdk_node"] = _mk_fragment(
            "sdk_node",
            implementations={BackendLanguage.NODE: FragmentImplSpec(fragment_dir="n")},
        )
        fragments["sdk_rust"] = _mk_fragment(
            "sdk_rust",
            implementations={BackendLanguage.RUST: FragmentImplSpec(fragment_dir="r")},
        )
        # Multi-fragment enables → discriminator/bundle fanout.
        options["auth.mode"] = Option(
            path="auth.mode",
            type=OptionType.ENUM,
            default="generate",
            options=("generate", "none"),
            summary="auth.mode",
            description="auth.mode",
            category=FeatureCategory.PLATFORM,
            enables={"generate": ("sdk_python", "sdk_node", "sdk_rust")},
        )
        # User explicitly sets auth.mode=generate, but project only has Python.
        # include_keycloak=True keeps the auth.mode→none coercion (which fires
        # when keycloak is off) inert — this test exercises discriminator fanout.
        cfg = _project([BackendLanguage.PYTHON], {"auth.mode": "generate"})
        cfg.include_keycloak = True
        plan = resolve(cfg)
        # Only the Python SDK was applied — Node and Rust silently skipped.
        applied = sorted(rf.fragment.name for rf in plan.ordered)
        assert applied == ["sdk_python"], (
            f"discriminator fanout must skip incompatible-backend fragments silently, got {applied}"
        )

    def test_target_backends_preserves_project_order(self, isolated_registries) -> None:
        options, fragments = isolated_registries
        fragments["poly"] = _mk_fragment(
            "poly",
            implementations={
                BackendLanguage.PYTHON: FragmentImplSpec(fragment_dir="p"),
                BackendLanguage.RUST: FragmentImplSpec(fragment_dir="r"),
            },
        )
        options["p.on"] = _mk_option("p.on", fragments=("poly",), default=True)
        plan = resolve(_project([BackendLanguage.RUST, BackendLanguage.PYTHON]))
        assert plan.ordered[0].target_backends == (BackendLanguage.RUST, BackendLanguage.PYTHON)


class TestOriginAwareSelection:
    """WS2b — `_is_user_selected` consults ``option_origins`` to tell
    user-set options apart from defaulted-but-persisted ones.

    The bug being fixed: ``forge --update`` reads forge.toml and
    constructs a ProjectConfig with ``options`` populated from the
    persisted (post-defaulting) values. A Python-only option like
    ``middleware.correlation_id`` is always-on by default, so a fresh-
    generated Node-only project's forge.toml carries it. On re-read
    that looks indistinguishable from a user choice — pre-WS2b the
    resolver would raise OPTIONS_INVALID_VALUE because the fragment
    has no Node implementation. With origins=default the resolver
    silently skips it, matching the fresh-generate behavior.
    """

    def test_user_set_single_fragment_option_on_incompatible_backend_errors(
        self, isolated_registries
    ) -> None:
        """Regression guard: explicit user selection still hard-errors.

        Single-fragment options the user actively chose must continue
        to surface the OPTIONS_INVALID_VALUE error — that's a real user
        mistake worth telling them about.
        """
        options, fragments = isolated_registries
        fragments["py_only"] = _mk_fragment(
            "py_only",
            implementations={BackendLanguage.PYTHON: FragmentImplSpec(fragment_dir="p")},
        )
        options["p.on"] = _mk_option("p.on", fragments=("py_only",), default=True)
        with pytest.raises(OptionsError, match="supported backends"):
            resolve(
                _project(
                    [BackendLanguage.RUST],
                    options={"p.on": True},
                    option_origins={"p.on": "user"},
                )
            )

    def test_defaulted_single_fragment_option_on_incompatible_backend_skips(
        self, isolated_registries
    ) -> None:
        """The lane E bug: persisted-default values don't trip the resolver.

        Mirrors the real-world ``middleware.correlation_id`` scenario:
        the manifest carries the option (default value), no Python
        backend present, the resolver must skip the fragment silently.
        """
        options, fragments = isolated_registries
        fragments["py_only"] = _mk_fragment(
            "py_only",
            implementations={BackendLanguage.PYTHON: FragmentImplSpec(fragment_dir="p")},
        )
        options["p.on"] = _mk_option("p.on", fragments=("py_only",), default=True)
        plan = resolve(
            _project(
                [BackendLanguage.RUST],
                options={"p.on": True},
                option_origins={"p.on": "default"},
            )
        )
        # Fragment is Python-only; with no Python backend present and
        # origin=default, the resolved plan must not include it.
        assert plan.ordered == ()

    def test_defaulted_second_fragment_skips_when_other_user_set(self, isolated_registries) -> None:
        """Mixed origins: per-key check (not whole-config gate).

        One option user-set + compatible, another defaulted + incompatible
        — only the user-set one drives the plan; the defaulted one
        silently skips. Guards against a regression where the origin
        check was accidentally hoisted to a config-wide gate.
        """
        options, fragments = isolated_registries
        fragments["py_only"] = _mk_fragment(
            "py_only",
            implementations={BackendLanguage.PYTHON: FragmentImplSpec(fragment_dir="p")},
        )
        fragments["rust_ok"] = _mk_fragment(
            "rust_ok",
            implementations={BackendLanguage.RUST: FragmentImplSpec(fragment_dir="r")},
        )
        options["p.py"] = _mk_option("p.py", fragments=("py_only",), default=True)
        options["p.rust"] = _mk_option("p.rust", fragments=("rust_ok",), default=False)
        plan = resolve(
            _project(
                [BackendLanguage.RUST],
                options={"p.py": True, "p.rust": True},
                option_origins={"p.py": "default", "p.rust": "user"},
            )
        )
        applied = [rf.fragment.name for rf in plan.ordered]
        assert applied == ["rust_ok"]

    def test_missing_origin_falls_back_to_user(self, isolated_registries) -> None:
        """Empty/missing origins map preserves pre-WS2 behavior.

        Existing test fixtures and ad-hoc ProjectConfig construction
        without ``option_origins`` must keep working — every option
        present is treated as user-set, matching the pre-WS2 default.
        """
        options, fragments = isolated_registries
        fragments["py_only"] = _mk_fragment(
            "py_only",
            implementations={BackendLanguage.PYTHON: FragmentImplSpec(fragment_dir="p")},
        )
        options["p.on"] = _mk_option("p.on", fragments=("py_only",), default=False)
        with pytest.raises(OptionsError, match="supported backends"):
            resolve(
                _project(
                    [BackendLanguage.RUST],
                    options={"p.on": True},
                    # No option_origins — fall back to "user".
                )
            )

    def test_discriminator_fanout_unaffected_by_origins(self, isolated_registries) -> None:
        """Discriminator (multi-fragment) options still silently fan out.

        Origins gate the user-set check; the discriminator-vs-single-
        fragment heuristic still distinguishes bundle intent from
        single-fragment selection. A user-set discriminator option whose
        per-language fanout includes incompatible fragments still skips
        them silently — origin=user doesn't escalate that to an error.
        """
        options, fragments = isolated_registries
        fragments["sdk_python"] = _mk_fragment(
            "sdk_python",
            implementations={BackendLanguage.PYTHON: FragmentImplSpec(fragment_dir="p")},
        )
        fragments["sdk_node"] = _mk_fragment(
            "sdk_node",
            implementations={BackendLanguage.NODE: FragmentImplSpec(fragment_dir="n")},
        )
        options["auth.mode"] = Option(
            path="auth.mode",
            type=OptionType.ENUM,
            default="generate",
            options=("generate", "none"),
            summary="auth.mode",
            description="auth.mode",
            category=FeatureCategory.PLATFORM,
            enables={"generate": ("sdk_python", "sdk_node")},
        )
        # User explicitly picked generate; only Node backend present.
        # include_keycloak=True keeps the auth.mode→none coercion inert.
        cfg = _project(
            [BackendLanguage.NODE],
            options={"auth.mode": "generate"},
            option_origins={"auth.mode": "user"},
        )
        cfg.include_keycloak = True
        plan = resolve(cfg)
        applied = sorted(rf.fragment.name for rf in plan.ordered)
        assert applied == ["sdk_node"]


class TestComponentResolution:
    """resolve() expands ProjectConfig.components into their emitter fragments
    (additive; empty components leaves the existing flow byte-identical)."""

    def _setup(self, fragments, comp_reg, nodes):
        from forge.components import component_fragments

        for node in nodes:
            comp_reg[node.name] = node
            frag = component_fragments(node)[0]
            fragments[frag.name] = frag

    def test_selected_component_fragment_enters_plan(self, isolated_registries) -> None:
        options, fragments = isolated_registries
        from forge.components import ComponentNode

        comp_reg: dict = {}
        self._setup(fragments, comp_reg, [ComponentNode(name="Card", layer=1)])
        cfg = _project([BackendLanguage.PYTHON])
        cfg.components = ["Card"]
        with patch("forge.components.COMPONENT_REGISTRY", comp_reg):
            plan = resolve(cfg)
        assert any(rf.fragment.name == "component_Card" for rf in plan.ordered)

    def test_vue_component_kept_on_non_python_backend(self, isolated_registries) -> None:
        # Regression: a Vue component compiles to a project-scoped, VUE-gated
        # fragment with a proxy PYTHON impl. On a Vue + Node-only project its
        # backend target-set is empty — it must still be kept (applies to
        # apps/<slug>/), not silently dropped.
        from forge.components import ComponentNode
        from forge.config import FrontendConfig, FrontendFramework

        options, fragments = isolated_registries
        comp_reg: dict = {}
        self._setup(fragments, comp_reg, [ComponentNode(name="Card", layer=1)])
        cfg = _project([BackendLanguage.NODE])
        cfg.frontend = FrontendConfig(framework=FrontendFramework.VUE, project_name="P")
        cfg.components = ["Card"]
        with patch("forge.components.COMPONENT_REGISTRY", comp_reg):
            plan = resolve(cfg)
        kept = [rf for rf in plan.ordered if rf.fragment.name == "component_Card"]
        assert len(kept) == 1, "Vue component dropped on a non-Python backend project"
        # Targeted at a single (proxy) language so it applies exactly once.
        assert len(kept[0].target_backends) == 1

    def test_child_component_ordered_before_parent(self, isolated_registries) -> None:
        options, fragments = isolated_registries
        from forge.components import ComponentNode

        comp_reg: dict = {}
        self._setup(
            fragments,
            comp_reg,
            [
                ComponentNode(name="Leaf", layer=1),
                ComponentNode(name="Panel", layer=2, children={"Leaf": "*"}),
            ],
        )
        cfg = _project([BackendLanguage.PYTHON])
        cfg.components = ["Panel"]
        with patch("forge.components.COMPONENT_REGISTRY", comp_reg):
            plan = resolve(cfg)
        names = [rf.fragment.name for rf in plan.ordered]
        assert "component_Leaf" in names and "component_Panel" in names
        assert names.index("component_Leaf") < names.index("component_Panel")

    def test_empty_components_is_noop(self, isolated_registries) -> None:
        options, fragments = isolated_registries
        cfg = _project([BackendLanguage.PYTHON])  # components defaults to []
        plan = resolve(cfg)
        assert plan.ordered == ()

    def test_unknown_component_raises(self, isolated_registries) -> None:
        from forge.errors import PluginError

        cfg = _project([BackendLanguage.PYTHON])
        cfg.components = ["Ghost"]
        with patch("forge.components.COMPONENT_REGISTRY", {}), pytest.raises(PluginError):
            resolve(cfg)


class TestAuthKeycloakCoercion:
    """include_keycloak=False ⇒ auth.mode coerced to 'none' at resolve time.

    The platform-auth gatekeeper stack depends on the keycloak + redis services,
    which only render under include_keycloak. The CLI builder already coerces
    auth.mode→none when keycloak is off; resolve() applies the SAME coercion so
    direct ProjectConfig/generate() construction (matrix runner, headless
    fixtures, e2e) also produces a valid docker-compose — no gatekeeper service
    with an undefined depends_on.
    """

    @pytest.fixture(autouse=True)
    def _features(self):
        from forge import feature_loader

        feature_loader.reset_for_tests()
        feature_loader.load_builtin_features()
        yield
        feature_loader.reset_for_tests()

    def _cfg(self, *, include_keycloak: bool, options=None) -> ProjectConfig:
        return ProjectConfig(
            project_name="P",
            backends=[BackendConfig(name="svc", project_name="P", language=BackendLanguage.PYTHON)],
            frontends=[],
            include_keycloak=include_keycloak,
            keycloak_port=18080,
            options=options or {},
        )

    def test_no_keycloak_coerces_auth_mode_none(self) -> None:
        plan = resolve(self._cfg(include_keycloak=False))
        assert plan.option_values["auth.mode"] == "none"
        # The gatekeeper capability/service must NOT be provisioned.
        assert "gatekeeper" not in plan.capabilities

    def test_keycloak_keeps_generate(self) -> None:
        plan = resolve(self._cfg(include_keycloak=True))
        assert plan.option_values["auth.mode"] == "generate"
        assert "gatekeeper" in plan.capabilities

    def test_explicit_generate_without_keycloak_still_coerced(self) -> None:
        # Matches the CLI: an explicit auth.mode=generate with no keycloak is
        # coerced to none rather than emitting an orphaned gatekeeper.
        plan = resolve(self._cfg(include_keycloak=False, options={"auth.mode": "generate"}))
        assert plan.option_values["auth.mode"] == "none"
        assert "gatekeeper" not in plan.capabilities


class TestMcpAuthGuardWithCoercion:
    """The MCP-requires-auth guard checks the EFFECTIVE auth.mode.

    Regression for the coercion interaction: mcp + auth.mode=generate +
    include_keycloak=False used to slip past the guard because it read raw
    config.options (still 'generate') while the effective auth.mode coerced to
    'none' — an unauthenticated MCP server. The guard now reads option_values.
    """

    @pytest.fixture(autouse=True)
    def _features(self):
        from forge import feature_loader

        feature_loader.reset_for_tests()
        feature_loader.load_builtin_features()
        yield
        feature_loader.reset_for_tests()

    def test_mcp_without_keycloak_raises_not_silently_unauthed(self) -> None:
        cfg = ProjectConfig(
            project_name="P",
            backends=[BackendConfig(name="svc", project_name="P", language=BackendLanguage.PYTHON)],
            include_keycloak=False,
            options={"platform.mcp": True, "auth.mode": "generate"},
        )
        with pytest.raises(OptionsError):
            resolve(cfg)

    def test_mcp_with_keycloak_resolves(self) -> None:
        cfg = ProjectConfig(
            project_name="P",
            backends=[BackendConfig(name="svc", project_name="P", language=BackendLanguage.PYTHON)],
            include_keycloak=True,
            options={"platform.mcp": True, "auth.mode": "generate"},
        )
        plan = resolve(cfg)  # auth stays generate → guard satisfied
        assert plan.option_values["auth.mode"] == "generate"


# --- backend-scoped conflicts (llm/queue/cache shared Rust src/ports/mod.rs) --


class TestBackendScopedConflicts:
    """llm + cache (and llm + queue) coexist on ALL three backends. The old
    Rust-only ``conflicts_with`` mutex existed because the llm Rust fragments
    overwrote the shared ``src/ports/mod.rs`` / ``src/adapters/mod.rs``; #236
    converted them to inject into those shared files like queue/cache do, so
    the mutex is retired and Rust behaves like Python/Node."""

    @staticmethod
    def _cfg(lang: BackendLanguage):
        return ProjectConfig(
            project_name="p",
            backends=[BackendConfig(name="b", project_name="p", language=lang)],
            options={"llm.provider": "openai", "reliability.cache": "redis"},
            option_origins={"llm.provider": "user", "reliability.cache": "user"},
        )

    def test_python_llm_plus_cache_coexist(self):
        plan = resolve(self._cfg(BackendLanguage.PYTHON))
        names = {rf.fragment.name for rf in plan.ordered}
        assert {"llm_port", "cache_port"} <= names

    def test_node_llm_plus_cache_coexist(self):
        plan = resolve(self._cfg(BackendLanguage.NODE))
        names = {rf.fragment.name for rf in plan.ordered}
        assert {"llm_port", "cache_port"} <= names

    def test_rust_llm_plus_cache_coexist(self):
        # #236: shared mod.rs injection retired the Rust mutex.
        plan = resolve(self._cfg(BackendLanguage.RUST))
        names = {rf.fragment.name for rf in plan.ordered}
        assert {"llm_port", "cache_port"} <= names

    def test_backend_scoped_conflict_mechanism(self):
        """The ``conflict_backends`` resolver mechanism still works for any
        fragment that opts in — even though no built-in uses it now (#236
        retired the only ones). A scoped conflict fires only when both
        fragments land on a project backend of the scoped language; on any
        other backend they coexist."""
        from forge.capability_resolver import _check_conflicts
        from forge.fragments import FRAGMENT_REGISTRY

        impls = {
            BackendLanguage.PYTHON: FragmentImplSpec(fragment_dir="syn/python"),
            BackendLanguage.RUST: FragmentImplSpec(fragment_dir="syn/rust"),
        }
        a = _mk_fragment(
            "syn_a",
            implementations=impls,
            conflicts_with=("syn_b",),
            conflict_backends=(BackendLanguage.RUST,),
        )
        b = _mk_fragment("syn_b", implementations=impls)
        with patch.dict(FRAGMENT_REGISTRY, {"syn_a": a, "syn_b": b}):
            # Both land on the scoped (Rust) backend → conflict fires.
            with pytest.raises(OptionsError, match="conflict"):
                _check_conflicts({"syn_a", "syn_b"}, (BackendLanguage.RUST,))
            # Same pair on a non-scoped backend → no conflict.
            _check_conflicts({"syn_a", "syn_b"}, (BackendLanguage.PYTHON,))


class TestQueueObjectStoreFailFast:
    """queue.backend / object_store.backend ship single-language adapters; a
    wrong-language selection used to silently resolve to an adapter-less port
    (or nothing). It must hard-error at config time instead."""

    @staticmethod
    def _cfg(lang: BackendLanguage, opts: dict):
        return ProjectConfig(
            project_name="p",
            backends=[BackendConfig(name="b", project_name="p", language=lang)],
            options=opts,
            option_origins={k: "user" for k in opts},
        )

    @pytest.mark.parametrize(
        "lang,value,ok",
        [
            (BackendLanguage.PYTHON, "redis", True),
            (BackendLanguage.NODE, "redis", False),
            (BackendLanguage.NODE, "bullmq", True),
            (BackendLanguage.PYTHON, "bullmq", False),
            (BackendLanguage.RUST, "apalis", True),
            (BackendLanguage.NODE, "apalis", False),
            (BackendLanguage.GO, "asynq", True),
            (BackendLanguage.RUST, "asynq", False),
            (BackendLanguage.GO, "apalis", False),
        ],
    )
    def test_queue_backend_requires_matching_language(self, lang, value, ok):
        cfg = self._cfg(lang, {"queue.backend": value})
        if ok:
            resolve(cfg)
        else:
            with pytest.raises(OptionsError):
                resolve(cfg)

    @pytest.mark.parametrize(
        "lang,ok",
        [
            (BackendLanguage.PYTHON, True),
            (BackendLanguage.NODE, False),
            (BackendLanguage.RUST, False),
            (BackendLanguage.GO, False),
        ],
    )
    def test_object_store_is_python_only(self, lang, ok):
        cfg = self._cfg(lang, {"object_store.backend": "s3"})
        if ok:
            resolve(cfg)
        else:
            with pytest.raises(OptionsError):
                resolve(cfg)

    def test_defaulted_value_never_hard_errors(self):
        # Same value as a persisted DEFAULT (origin != user) must not error.
        cfg = ProjectConfig(
            project_name="p",
            backends=[BackendConfig(name="b", project_name="p", language=BackendLanguage.NODE)],
            options={"queue.backend": "redis"},
            option_origins={"queue.backend": "default"},
        )
        resolve(cfg)  # no raise
