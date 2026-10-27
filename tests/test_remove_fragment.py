"""Tests for ``forge --remove-fragment NAME`` (P1.2, 1.1.0-alpha.2)."""

from __future__ import annotations

import pytest

from forge.cli.commands.remove_fragment import _options_that_enable
from forge.options import OPTION_REGISTRY


class TestOptionsThatEnable:
    """Lookup helper — find every option whose ``enables`` map references
    a given fragment."""

    def test_finds_single_enabling_option(self) -> None:
        # ``rate_limit`` fragment is enabled exactly by middleware.rate_limit.
        result = _options_that_enable("rate_limit", OPTION_REGISTRY)
        paths = [path for path, _opt, _val in result]
        assert "middleware.rate_limit" in paths

    def test_finds_zero_for_unknown_fragment(self) -> None:
        result = _options_that_enable("not_a_real_fragment", OPTION_REGISTRY)
        assert result == []

    def test_finds_multiple_for_shared_dep(self) -> None:
        # ``conversation_persistence`` is enabled by conversation.persistence
        # AND every rag.backend != none AND chat.attachments. We don't
        # assert a specific count — only that more than one option enables it,
        # which is the trigger for --remove-fragment's "ambiguous" error.
        result = _options_that_enable("conversation_persistence", OPTION_REGISTRY)
        assert len(result) >= 2

    def test_returns_value_that_enables(self) -> None:
        # ``rag_pipeline`` is enabled when ``rag.backend`` != none.
        # We expect the result tuple's third element to capture the value
        # that enables (one of the rag.backend ENUM values).
        result = _options_that_enable("rag_pipeline", OPTION_REGISTRY)
        rag_paths = [
            (path, val) for path, _opt, val in result if path == "rag.backend"
        ]
        assert rag_paths
        # Value should be a non-default option of rag.backend.
        path, value = rag_paths[0]
        assert value in (
            "pgvector",
            "qdrant",
            "chroma",
            "milvus",
            "weaviate",
            "pinecone",
            "postgresql",
        )


def test_the_option_flip_keeps_every_frontend(tmp_path, monkeypatch) -> None:
    """``--remove-fragment`` rewrites forge.toml before updating; every app stays recorded."""
    import argparse

    import forge.sync.forge_to_project.updater as updater
    from forge.cli.commands.remove_fragment import _run_remove_fragment
    from forge.sync.manifest import ForgeFrontendData, read_forge_toml, write_forge_toml

    frontends = (
        ForgeFrontendData(framework="vue", app_dir="apps/frontend"),
        ForgeFrontendData(framework="vue", app_dir="apps/admin", ui_kit="nuxt-ui"),
    )
    write_forge_toml(
        tmp_path / "forge.toml",
        version="1.3.0",
        project_name="demo",
        templates={},
        # Not the default, so the command rewrites the manifest.
        options={"middleware.rate_limit": False},
        frontends=frontends,
    )
    monkeypatch.setattr(updater, "update_project", lambda *a, **k: {"uninstalled": []})
    with pytest.raises(SystemExit) as exit_info:
        _run_remove_fragment(
            argparse.Namespace(
                remove_fragment="rate_limit",
                project_path=str(tmp_path),
                quiet=True,
                json_output=False,
                update_mode="merge",
            )
        )
    assert exit_info.value.code == 0
    data = read_forge_toml(tmp_path / "forge.toml")
    assert data.options["middleware.rate_limit"] is True
    assert data.frontends == frontends
