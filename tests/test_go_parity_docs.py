"""Docs that describe Go behaviour stay in step with the code they describe."""

from __future__ import annotations

import re
from pathlib import Path

from forge.config import BackendLanguage
from forge.fragments import FRAGMENT_REGISTRY

_ROOT = Path(__file__).resolve().parent.parent
_GO = _ROOT / "forge" / "templates" / "services" / "go-service-template" / "template"
_RLS_GO = _ROOT / "forge" / "features" / "multitenancy" / "templates" / "multitenancy_rls_go" / "go"
_NAMES = {
    BackendLanguage.PYTHON: "Python",
    BackendLanguage.NODE: "Node",
    BackendLanguage.RUST: "Rust",
    BackendLanguage.GO: "Go",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_operations_lists_every_migrate_failure_with_its_exit_code() -> None:
    main = _read(_GO / "cmd" / "migrate" / "main.go.jinja")
    in_code = set(re.findall(r'slog\.Error\("([^"]+)".*\n\s*return (\d)', main))
    in_docs = set(re.findall(r"^\| (\d) \| `([^`]+)` \|", _read(_GO / "OPERATIONS.md"), re.M))

    assert len(in_code) == 4
    assert {(message, code) for code, message in in_docs} == in_code


def test_operations_quotes_the_rls_rollback_warning() -> None:
    migrations = _read(_GO / "migrations" / "migrations.go")
    warning = re.search(r'slog\.WarnContext\(ctx, "([^":]+)', migrations)

    assert warning is not None
    assert f"`{warning.group(1)}`" in _read(_GO / "OPERATIONS.md")


def test_operations_rls_sql_uses_the_runtime_setting() -> None:
    config = _read(_RLS_GO / "files" / "internal" / "tenancy" / "config.go.jinja")
    guc = re.search(r'DefaultGUC = "([^"]+)"', config)
    operations = _read(_GO / "OPERATIONS.md")

    assert guc is not None
    assert f"`TENANT_RLS_GUC` must stay `{guc.group(1)}`" in operations
    assert operations.count(f"NULLIF(current_setting('{guc.group(1)}', true), '')::uuid") == 2
    assert f"'{guc.group(1)}'" in _read(_RLS_GO / "files" / "migrations" / "00099_enable_rls.sql")


def test_openai_backends_match_the_llm_openai_fragment() -> None:
    languages = "/".join(
        _NAMES[language]
        for language in BackendLanguage
        if language in FRAGMENT_REGISTRY["llm_openai"].implementations
    )

    assert f"OpenAI adapters exist for {languages};" in _read(_ROOT / "README.md")
    assert f"| OpenAI on {languages};" in _read(_ROOT / "docs" / "reference" / "limitations.md")


def test_toolchain_notes_name_every_compiled_backend() -> None:
    compiled = [
        _NAMES[language] for language in BackendLanguage if language != BackendLanguage.PYTHON
    ]
    toolchains = ", ".join(compiled)
    guide = _ROOT / "docs" / "guides" / "getting-started.md"

    assert f"selected {toolchains}, or Flutter toolchains" in _read(_ROOT / "README.md")
    assert f"also require {toolchains}, or Flutter" in _read(guide)
