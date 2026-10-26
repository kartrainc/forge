"""Theme 2A — ``agent.mode`` discriminator tests.

Locks in the four-value enum + fragment bundles per value + the
cross-layer rule that ``agent.mode != "none"`` requires
``backend.mode != "none"``. Companion to the layer-mode parity tests
in ``tests/test_phase_c.py::TestLayerModeParity``; this file is the
narrow Theme 2A surface (the parity tests cover the shared shape).

Coverage:

* Registry: ``agent.mode`` has all four values, defaults to ``none``,
  and is an ENUM under the conversational-AI category.
* Bundles: each enum value resolves to the expected fragment set.
  ``none`` and ``multi_agent`` are empty bundles; ``llm_only`` ships
  the LLM port + chat history; ``tool_calling`` ships the full agent
  triple + MCP scaffolds.
* Validator: ``agent.mode != "none"`` requires a backend,
  ``multi_agent`` raises NOT-YET-IMPLEMENTED at validate time.
* Resolver: end-to-end ``ProjectConfig.options["agent.mode"]=...``
  produces a ResolvedPlan whose fragment names include the bundle.
"""

from __future__ import annotations

import pytest

from forge.capability_resolver import resolve
from forge.config import BackendConfig, BackendLanguage, ProjectConfig
from forge.options import OPTION_REGISTRY, OptionType
from forge.options.agent import _AGENT_MODE_ENABLES


# Fragment bundles expected per agent.mode value. Mirrors the
# definition in ``forge/options/agent/__init__.py`` so the tests catch
# drift in either direction.
_EXPECTED_BUNDLES: dict[str, set[str]] = {
    "none": set(),
    "llm_only": {"llm_port", "conversation_persistence"},
    "tool_calling": {
        "llm_port",
        "conversation_persistence",
        "agent_streaming",
        "agent_tools",
        "agent",
        "mcp_server",
        "mcp_ui",
    },
    "multi_agent": set(),
}


def _python_project(options: dict[str, object] | None = None) -> ProjectConfig:
    options = options or {}
    # agent.mode=tool_calling (and multi_agent) pull in the MCP server, which the
    # security guard requires auth for; platform-auth in turn requires Keycloak
    # (the resolver coerces auth.mode→none when keycloak is off). Enable keycloak
    # for the auth-bearing configs so the MCP guard is satisfied.
    needs_auth = (
        options.get("auth.mode") == "generate"
        or options.get("platform.mcp") is True
        or options.get("agent.mode") in ("tool_calling", "multi_agent")
    )
    return ProjectConfig(
        project_name="P",
        backends=[
            BackendConfig(
                name="svc",
                project_name="P",
                language=BackendLanguage.PYTHON,
                # 5010, not 5000: a keycloak-enabled stack publishes gatekeeper on
                # host 5000, which the port validator now (correctly) reserves.
                server_port=5010,
            )
        ],
        frontend=None,
        include_keycloak=needs_auth,
        options=options,
    )


# -- Registry shape -----------------------------------------------------------


class TestAgentModeRegistration:
    def test_registered_in_registry(self):
        assert "agent.mode" in OPTION_REGISTRY

    def test_is_enum(self):
        opt = OPTION_REGISTRY["agent.mode"]
        assert opt.type is OptionType.ENUM

    def test_default_is_none(self):
        opt = OPTION_REGISTRY["agent.mode"]
        assert opt.default == "none"

    def test_has_four_values(self):
        opt = OPTION_REGISTRY["agent.mode"]
        assert set(opt.options) == {"none", "llm_only", "tool_calling", "multi_agent"}

    def test_category_is_conversational_ai(self):
        opt = OPTION_REGISTRY["agent.mode"]
        # The umbrella category for the agentic stack — same as the
        # fine-grained agent.* / llm.* options.
        from forge.options import FeatureCategory  # noqa: PLC0415

        assert opt.category is FeatureCategory.CONVERSATIONAL_AI


# -- Fragment bundles ---------------------------------------------------------


class TestAgentModeBundles:
    @pytest.mark.parametrize("value,expected", sorted(_EXPECTED_BUNDLES.items()))
    def test_bundle_matches_expected(self, value: str, expected: set[str]):
        """The ``enables`` map of ``agent.mode`` declares the right
        fragments for each value. Source-of-truth comparison: changes
        to ``_AGENT_MODE_ENABLES`` must update ``_EXPECTED_BUNDLES``
        here too — both sides of the contract move together."""
        opt = OPTION_REGISTRY["agent.mode"]
        assert set(opt.enables.get(value, ())) == expected

    def test_internal_constant_matches_registered_enables(self):
        """The ``_AGENT_MODE_ENABLES`` constant exported from
        ``forge.options.agent`` matches what was passed to
        ``register_option``."""
        opt = OPTION_REGISTRY["agent.mode"]
        assert opt.enables == _AGENT_MODE_ENABLES

    def test_none_bundle_is_empty(self):
        """The shared layer-mode invariant: ``none`` enables no fragments."""
        opt = OPTION_REGISTRY["agent.mode"]
        assert opt.enables.get("none", ()) == ()

    def test_multi_agent_bundle_is_empty(self):
        """``multi_agent`` is a placeholder value — fragment wiring is
        deferred to v2. The bundle is empty so the resolver doesn't
        accidentally pull in fragments; the cross-layer validator
        raises NOT-YET-IMPLEMENTED before generation runs."""
        opt = OPTION_REGISTRY["agent.mode"]
        assert opt.enables.get("multi_agent", ()) == ()


# -- Cross-layer validation ---------------------------------------------------


class TestAgentModeCrossLayer:
    @pytest.mark.parametrize("agent_value", ["llm_only", "tool_calling", "multi_agent"])
    def test_non_none_agent_mode_requires_backend(self, agent_value: str):
        """Frontend-only projects (``backend.mode=none``) cannot host the
        agent loop. The validator surfaces this as a ValueError with a
        message that names both surfaces so the user knows where to
        flip the switch."""
        from forge.config._frontend import FrontendConfig, FrontendFramework  # noqa: PLC0415

        # backend.mode=none + frontend=vue + api_target.url=<external>.
        config = ProjectConfig(
            project_name="P",
            backends=[],
            frontend=FrontendConfig(
                framework=FrontendFramework.VUE,
                project_name="P",
                features=[],
                server_port=3000,
            ),
            options={
                "backend.mode": "none",
                "frontend.api_target.url": "https://api.example.com",
                "agent.mode": agent_value,
            },
        )
        with pytest.raises(ValueError, match=r"agent\.mode=.*requires backend\.mode"):
            config.validate()

    def test_multi_agent_raises_not_yet_implemented(self):
        """``multi_agent`` is registered for forward-compat but
        unimplemented; validate() fails fast with a clear message."""
        config = _python_project({"agent.mode": "multi_agent"})
        with pytest.raises(ValueError, match=r"multi_agent.*not yet implemented"):
            config.validate()

    def test_none_with_no_backend_is_fine(self):
        """``agent.mode=none`` (the default) imposes no backend rule —
        the cross-layer check is only triggered for non-none modes."""
        from forge.config._frontend import FrontendConfig, FrontendFramework  # noqa: PLC0415

        config = ProjectConfig(
            project_name="P",
            backends=[],
            frontend=FrontendConfig(
                framework=FrontendFramework.VUE,
                project_name="P",
                features=[],
                server_port=3000,
            ),
            options={
                "backend.mode": "none",
                "frontend.api_target.url": "https://api.example.com",
                "agent.mode": "none",
            },
        )
        config.validate()  # no raise

    @pytest.mark.parametrize("agent_value", ["llm_only", "tool_calling"])
    def test_non_none_agent_mode_with_backend_passes(self, agent_value: str):
        """When a backend is present, ``llm_only`` and ``tool_calling``
        validate cleanly. ``multi_agent`` is excluded because it raises
        the unimplemented error even with a backend in place."""
        config = _python_project({"agent.mode": agent_value})
        config.validate()  # no raise


# -- Resolver end-to-end ------------------------------------------------------


class TestAgentModeResolves:
    def test_none_enables_no_agent_fragments(self):
        plan = resolve(_python_project({"agent.mode": "none"}))
        applied = {rf.fragment.name for rf in plan.ordered}
        # The bundle is empty; none of the agent.mode bundle fragments
        # should appear (unless pulled in by some other default, which
        # they shouldn't be — every agent.* flag defaults to False and
        # llm.provider defaults to "none").
        for frag in _EXPECTED_BUNDLES["tool_calling"]:
            assert frag not in applied, f"unexpected fragment {frag!r} with agent.mode=none"

    def test_llm_only_enables_llm_port_and_chat_history(self):
        plan = resolve(_python_project({"agent.mode": "llm_only"}))
        applied = {rf.fragment.name for rf in plan.ordered}
        assert _EXPECTED_BUNDLES["llm_only"].issubset(applied)
        # Tool-calling-only fragments should NOT be in the llm_only plan.
        for frag in ("agent", "agent_streaming", "agent_tools", "mcp_server", "mcp_ui"):
            assert frag not in applied, f"{frag!r} leaked into agent.mode=llm_only"

    def test_tool_calling_enables_full_bundle(self):
        plan = resolve(_python_project({"agent.mode": "tool_calling"}))
        applied = {rf.fragment.name for rf in plan.ordered}
        assert _EXPECTED_BUNDLES["tool_calling"].issubset(applied)

    def test_tool_calling_pulls_transitive_conversation_persistence(self):
        """``agent_streaming`` declares ``depends_on=("conversation_persistence",)``;
        the resolver pulls that automatically. Tool_calling lists it
        explicitly too, so the depends_on path is belt-and-braces — but
        we still verify the fragment is in the plan."""
        plan = resolve(_python_project({"agent.mode": "tool_calling"}))
        applied = {rf.fragment.name for rf in plan.ordered}
        assert "conversation_persistence" in applied

    def test_user_flag_still_works_alongside_mode(self):
        """Backwards compatibility: setting both ``agent.mode=llm_only``
        and ``agent.streaming=true`` is not an error — the resolver
        de-dupes by fragment name. The final plan contains both the
        bundle's fragments and the flag's fragments."""
        plan = resolve(
            _python_project(
                {
                    "agent.mode": "llm_only",
                    "agent.streaming": True,
                }
            )
        )
        applied = {rf.fragment.name for rf in plan.ordered}
        # llm_only bundle:
        assert "llm_port" in applied
        assert "conversation_persistence" in applied
        # agent.streaming=True flag pulled agent_streaming in:
        assert "agent_streaming" in applied
