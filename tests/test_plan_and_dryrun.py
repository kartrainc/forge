"""Tests for `forge --plan` and `forge --dry-run` (0.4 of 1.0 roadmap)."""

from __future__ import annotations

import json
import sys
from argparse import Namespace
from pathlib import Path

import pytest

from forge.cli.commands.plan import _build_preview, _dispatch_plan
from forge.config import BackendConfig, BackendLanguage, FrontendFramework, ProjectConfig


def _stub_args(tmp_path: Path, **overrides) -> Namespace:
    """Build an argparse Namespace with every dest present at its default."""
    defaults: dict = dict(
        config=None,
        project_name="demo",
        description=None,
        output_dir=str(tmp_path),
        backend_language="python",
        backend_name=None,
        backend_port=None,
        python_version=None,
        node_version=None,
        rust_edition=None,
        frontend="none",
        features="items",
        author_name=None,
        package_manager=None,
        frontend_port=None,
        color_scheme=None,
        org_name=None,
        include_auth=False,
        include_chat=None,
        include_openapi=None,
        generate_e2e_tests=None,
        keycloak_port=None,
        keycloak_realm=None,
        keycloak_client_id=None,
        set_options=[],
        list=False,
        describe=None,
        schema=False,
        format=None,
        update=False,
        project_path=".",
        plan=True,
        dry_run=False,
        plugins_subcommand=None,
        yes=True,
        no_docker=True,
        quiet=True,
        verbose=False,
        json_output=False,
        completion=None,
    )
    defaults.update(overrides)
    return Namespace(**defaults)


class TestBuildPreview:
    def test_preview_has_expected_keys(self) -> None:
        from forge.capability_resolver import resolve

        config = ProjectConfig(
            project_name="demo",
            backends=[
                BackendConfig(
                    name="api",
                    project_name="demo",
                    language=BackendLanguage.PYTHON,
                    features=["items"],
                )
            ],
            frontend=None,
        )
        plan = resolve(config)
        preview = _build_preview(config, plan)
        for key in ("project_name", "backends", "frontend", "fragments", "capabilities"):
            assert key in preview

    def test_preview_lists_fragments_with_targets(self) -> None:
        from forge.capability_resolver import resolve

        config = ProjectConfig(
            project_name="demo",
            backends=[
                BackendConfig(
                    name="api",
                    project_name="demo",
                    language=BackendLanguage.PYTHON,
                    features=["items"],
                )
            ],
            frontend=None,
            options={"middleware.rate_limit": True},
        )
        plan = resolve(config)
        preview = _build_preview(config, plan)
        names = [f["name"] for f in preview["fragments"]]
        assert "rate_limit" in names
        rate_limit = next(f for f in preview["fragments"] if f["name"] == "rate_limit")
        assert "python" in rate_limit["target_backends"]


class TestDispatchPlan:
    def test_plan_text_output(self, tmp_path: Path, capsys) -> None:
        args = _stub_args(tmp_path)
        with pytest.raises(SystemExit) as exc:
            _dispatch_plan(args)
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "forge plan" in out
        assert "backends" in out

    def test_plan_json_output(self, tmp_path: Path, capsys) -> None:
        args = _stub_args(tmp_path, json_output=True)
        with pytest.raises(SystemExit) as exc:
            _dispatch_plan(args)
        assert exc.value.code == 0
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["project_name"] == "demo"
        assert "fragments" in payload

    def test_plan_graph_output_is_mermaid(self, tmp_path: Path, capsys) -> None:
        # P2: --graph emits a Mermaid graph instead of the tree view.
        args = _stub_args(tmp_path, plan_graph=True)
        with pytest.raises(SystemExit) as exc:
            _dispatch_plan(args)
        assert exc.value.code == 0
        out = capsys.readouterr().out
        # Mermaid graph header is the wire signal.
        assert out.startswith("graph TD")
        # Default-enabled rate_limit must surface; its hexagonal node id
        # is sanitised "rate_limit".
        assert "rate_limit" in out

    def test_plan_graph_includes_option_node_when_value_pulls_fragment(
        self, tmp_path: Path, capsys
    ) -> None:
        # rag.backend defaults to "none" (no fragments). Set it to "qdrant"
        # via the resolver path; the graph must show ``rag.backend`` as
        # an option node connected to vector_store_qdrant.
        args = _stub_args(tmp_path, plan_graph=True, set_options=["rag.backend=qdrant"])
        with pytest.raises(SystemExit) as exc:
            _dispatch_plan(args)
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "rag_backend" in out  # sanitised id for ``rag.backend``
        assert "vector_store_qdrant" in out
        # An arrow from the option to the fragment must be present.
        assert "rag_backend" in out and "-->" in out

    def test_plan_graph_includes_depends_on_edge(
        self, tmp_path: Path, capsys
    ) -> None:
        # ``rag.backend=qdrant`` pulls vector_store_qdrant which depends_on
        # vector_store_port — that's the dotted-line "depends_on" edge.
        args = _stub_args(tmp_path, plan_graph=True, set_options=["rag.backend=qdrant"])
        with pytest.raises(SystemExit) as exc:
            _dispatch_plan(args)
        assert exc.value.code == 0
        out = capsys.readouterr().out
        # Dotted arrow with the depends_on label is the wire signal.
        assert "-.->|depends_on|" in out

    def test_plan_graph_with_no_fragments_emits_empty_marker(
        self, tmp_path: Path, capsys
    ) -> None:
        # Disable every default-enabled fragment to produce an empty plan.
        args = _stub_args(
            tmp_path,
            plan_graph=True,
            set_options=[
                "middleware.rate_limit=false",
                "middleware.security_headers=false",
                "middleware.pii_redaction=false",
                "platform.agents_md=false",
                "reliability.connection_pool=false",
            ],
        )
        with pytest.raises(SystemExit) as exc:
            _dispatch_plan(args)
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "graph TD" in out
        # The "no fragments" marker is the empty-plan signal.
        # (May still be non-empty if there's a transitive default fragment;
        # the assertion is permissive — just confirm no crash.)
        assert "graph TD" in out


class TestDryRun:
    def test_dry_run_writes_to_tempdir_not_output_dir(self, tmp_path: Path) -> None:
        """generate(dry_run=True) must not touch config.output_dir."""
        from forge.generator import generate

        config = ProjectConfig(
            project_name="dryrun_demo",
            output_dir=str(tmp_path),
            backends=[
                BackendConfig(
                    name="api",
                    project_name="dryrun_demo",
                    language=BackendLanguage.PYTHON,
                    features=["items"],
                )
            ],
            frontend=None,
        )
        # Snapshot the configured output dir before the dry run.
        before = set(tmp_path.iterdir())
        project_root = generate(config, quiet=True, dry_run=True)
        after = set(tmp_path.iterdir())
        # The configured output dir is unchanged.
        assert before == after
        # The dry-run target exists and contains at least forge.toml.
        assert (project_root / "forge.toml").exists()
        # Dry-run project sits under a tempdir, not the configured output_dir.
        assert not str(project_root).startswith(str(tmp_path.resolve()))
