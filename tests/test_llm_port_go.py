"""Go LLM port + OpenAI adapter (``llm_port`` / ``llm_openai``)."""

from __future__ import annotations

import pytest

from forge.capability_resolver import resolve
from forge.config import BackendConfig, BackendLanguage, ProjectConfig
from forge.errors import OptionsError
from forge.fragments import FRAGMENT_REGISTRY
from forge.generator import generate


def _go_project(options: dict[str, object]) -> ProjectConfig:
    return ProjectConfig(
        project_name="P",
        backends=[
            BackendConfig(
                name="api", project_name="P", language=BackendLanguage.GO, server_port=5000
            )
        ],
        frontend=None,
        options=options,
    )


def test_llm_fragments_ship_go_impls() -> None:
    port = FRAGMENT_REGISTRY["llm_port"].implementations[BackendLanguage.GO]
    adapter = FRAGMENT_REGISTRY["llm_openai"].implementations[BackendLanguage.GO]
    assert port.dependencies == ()
    assert adapter.dependencies == ("github.com/openai/openai-go/v3@v3.86.0",)
    assert {k for k, _ in adapter.env_vars} == {"OPENAI_API_KEY", "OPENAI_BASE_URL"}


@pytest.mark.parametrize("fragment", ["llm_anthropic", "llm_ollama", "llm_bedrock"])
def test_other_llm_adapters_stay_python_only(fragment: str) -> None:
    assert BackendLanguage.GO not in FRAGMENT_REGISTRY[fragment].implementations


def test_openai_provider_resolves_on_go_only_project() -> None:
    names = {rf.fragment.name for rf in resolve(_go_project({"llm.provider": "openai"})).ordered}
    assert {"llm_port", "llm_openai"} <= names


@pytest.mark.parametrize("provider", ["anthropic", "ollama", "bedrock"])
def test_python_only_providers_rejected_on_go_only_project(provider: str) -> None:
    with pytest.raises(OptionsError):
        resolve(_go_project({"llm.provider": provider}))


def test_generated_go_service_carries_port_adapter_and_tests() -> None:
    root = generate(_go_project({"llm.provider": "openai"}), quiet=True, dry_run=True)
    service = next(p.parent for p in root.rglob("go.mod"))
    port = service / "internal" / "ports" / "llm" / "llm.go"
    adapter = service / "internal" / "adapters" / "llmopenai" / "openai.go"
    assert "type Port interface" in port.read_text(encoding="utf-8")
    body = adapter.read_text(encoding="utf-8")
    assert '"api/internal/ports/llm"' in body
    assert "var _ llm.Port = (*Adapter)(nil)" in body
    for test in ("llm_port_test.go", "llm_openai_test.go"):
        text = (service / "tests" / "unit" / test).read_text(encoding="utf-8")
        assert "{%" not in text and "{{ project_name" not in text
    assert "github.com/openai/openai-go/v3 v3.86.0" in (service / "go.mod").read_text(
        encoding="utf-8"
    )
    env = (service / ".env.example").read_text(encoding="utf-8")
    assert "OPENAI_API_KEY=" in env and "OPENAI_BASE_URL=" in env
