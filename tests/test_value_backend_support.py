"""WS-7: ``_check_value_backend_support`` — reject user-selected option values
no project backend supports (the "fail at config time, not silently at runtime"
polyglot guard).

These exercise the function directly (it is pure over ``config`` +
``project_backends`` and reads module-level lookup tables), so they cover every
decision branch without the registry-mocking the broader resolver tests need.
This is also the mutation-testing target for the function's changed lines: each
test pins one branch so a mutant that flips it is killed.
"""

from __future__ import annotations

import pytest

from forge.capability_resolver import (
    _INACTIVE_VALUES,
    _PYTHON_ONLY_WHEN_ACTIVE,
    _VALUE_REQUIRES_BACKEND,
    _check_value_backend_support,
)
from forge.config import BackendConfig, BackendLanguage, ProjectConfig
from forge.errors import OptionsError


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
        frontend=None,
        options=options or {},
        option_origins=option_origins or {},
    )


def _check(config: ProjectConfig) -> None:
    _check_value_backend_support(config, tuple(b.language for b in config.backends))


# --- _VALUE_REQUIRES_BACKEND (exact path+value match) ------------------------


@pytest.mark.parametrize("provider", ["anthropic", "ollama", "bedrock"])
def test_python_only_provider_on_node_only_project_raises(provider: str) -> None:
    config = _project(
        [BackendLanguage.NODE],
        options={"llm.provider": provider},
        option_origins={"llm.provider": "user"},
    )
    with pytest.raises(OptionsError) as exc:
        _check(config)
    # The message names the offending option, the required backend, and what
    # the project actually has — pin all three so a mutant that drops any is
    # caught.
    msg = str(exc.value)
    assert "llm.provider" in msg
    assert provider in msg
    assert "python" in msg.lower()
    assert "node" in msg.lower()


def test_python_only_provider_with_python_backend_present_is_allowed() -> None:
    # NODE + PYTHON: python satisfies the requirement, so no raise even though
    # node alone wouldn't. Kills the ``present.isdisjoint(required)`` mutant.
    config = _project(
        [BackendLanguage.NODE, BackendLanguage.PYTHON],
        options={"llm.provider": "anthropic"},
        option_origins={"llm.provider": "user"},
    )
    _check(config)  # must not raise


def test_unconstrained_provider_value_is_allowed_on_any_backend() -> None:
    # "openai" is not in _VALUE_REQUIRES_BACKEND -> never constrained.
    config = _project(
        [BackendLanguage.RUST],
        options={"llm.provider": "openai"},
        option_origins={"llm.provider": "user"},
    )
    _check(config)  # must not raise


def test_persisted_default_value_never_hard_errors() -> None:
    # Same offending value, but origin is a persisted default, not a user
    # choice -> skipped. Kills the ``origins.get(path) != "user"`` mutant.
    config = _project(
        [BackendLanguage.NODE],
        options={"llm.provider": "anthropic"},
        option_origins={"llm.provider": "default"},
    )
    _check(config)  # must not raise


def test_missing_origin_defaults_to_user_and_is_checked() -> None:
    # No origin entry -> treated as user-selected (fail closed) -> raises.
    config = _project(
        [BackendLanguage.NODE],
        options={"llm.provider": "anthropic"},
        option_origins={},
    )
    with pytest.raises(OptionsError):
        _check(config)


# --- _PYTHON_ONLY_WHEN_ACTIVE (any active value is python-only) --------------


@pytest.mark.parametrize("path", ["rag.backend", "platform.mcp"])
def test_active_python_only_feature_on_rust_only_raises(path: str) -> None:
    config = _project(
        [BackendLanguage.RUST],
        options={path: "pgvector" if path == "rag.backend" else True},
        option_origins={path: "user"},
    )
    with pytest.raises(OptionsError) as exc:
        _check(config)
    assert path in str(exc.value)


@pytest.mark.parametrize("inactive", sorted(_INACTIVE_VALUES, key=repr))
def test_inactive_python_only_value_is_allowed(inactive: object) -> None:
    # An "off" value (None / "" / "none" / False) means the feature isn't
    # selected, so it must NOT raise even on a non-python project. Kills the
    # ``value not in _INACTIVE_VALUES`` mutant.
    config = _project(
        [BackendLanguage.NODE],
        options={"rag.backend": inactive},
        option_origins={"rag.backend": "user"},
    )
    _check(config)  # must not raise


def test_active_python_only_feature_with_python_present_is_allowed() -> None:
    config = _project(
        [BackendLanguage.RUST, BackendLanguage.PYTHON],
        options={"platform.mcp": True},
        option_origins={"platform.mcp": "user"},
    )
    _check(config)  # must not raise


# --- error payload -----------------------------------------------------------


def test_error_carries_machine_readable_context() -> None:
    config = _project(
        [BackendLanguage.NODE, BackendLanguage.RUST],
        options={"llm.provider": "anthropic"},
        option_origins={"llm.provider": "user"},
    )
    with pytest.raises(OptionsError) as exc:
        _check(config)
    ctx = exc.value.context
    assert ctx["option"] == "llm.provider"
    assert ctx["value"] == "anthropic"
    assert ctx["required_backends"] == ["python"]
    # Project backends are reported in declaration order.
    assert ctx["project_backends"] == ["node", "rust"]


# --- table integrity (guards the declarative lookup data) --------------------


def test_value_requires_backend_table_shape() -> None:
    # Every entry maps an (option_path, value) tuple to a non-empty frozenset
    # of BackendLanguage. Pins the table so a mutation to a key/value is a
    # visible contract change, not silent.
    for key, langs in _VALUE_REQUIRES_BACKEND.items():
        assert isinstance(key, tuple) and len(key) == 2
        assert isinstance(langs, frozenset) and langs
        assert all(isinstance(b, BackendLanguage) for b in langs)
    assert _VALUE_REQUIRES_BACKEND[("llm.provider", "anthropic")] == frozenset(
        {BackendLanguage.PYTHON}
    )


def test_python_only_when_active_table_shape() -> None:
    # object_store.backend joined the Python-only set in #219 (its port +
    # adapters are Python-only; selecting it on Node/Rust used to resolve to
    # zero fragments — a silent no-op).
    assert set(_PYTHON_ONLY_WHEN_ACTIVE) == {
        "rag.backend",
        "platform.mcp",
        "object_store.backend",
        # agent.mode joined the Python-only set (audit #16): its
        # llm_only/tool_calling bundles fan out to Python-only fragments, so on
        # a Node/Rust-only project they'd silently resolve to just llm_port.
        "agent.mode",
    }
    for langs in _PYTHON_ONLY_WHEN_ACTIVE.values():
        assert langs == frozenset({BackendLanguage.PYTHON})


def test_inactive_values_membership() -> None:
    assert None in _INACTIVE_VALUES
    assert "" in _INACTIVE_VALUES
    assert "none" in _INACTIVE_VALUES
    assert False in _INACTIVE_VALUES
    assert "pgvector" not in _INACTIVE_VALUES
