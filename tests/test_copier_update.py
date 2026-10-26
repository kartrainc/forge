"""Tests for Item 5 of happy-inventing-eclipse — Copier base-template re-render.

Phase 5 of the bidirectional-sync plan wraps :func:`copier.run_update`
so ``forge --update`` re-renders base templates in addition to
re-applying fragments. These tests cover:

* The template-version resolver (``_forge_template.toml`` wins over
  the BackendSpec default).
* Delta detection in :func:`update_project` (no delta → no Copier
  call; delta → Copier wrapper invoked).
* The ``--no-template-update`` opt-out.
* Conversion of Copier ``.rej`` files into ``.forge-merge`` sidecars.
* Provenance re-stamp after a successful template update.
* Failure handling: a Copier error aborts the wider update run.

Heavy lifting goes through :mod:`unittest.mock` so the wrapper's
signature can be asserted without spinning up a real Copier render in
every test. One integration-style test exercises the resolver + the
real updater entry point against a fixture project.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from forge.config import BackendConfig, BackendLanguage, ProjectConfig
from forge.errors import ProvenanceError
from forge.sync.forge_to_project.template_update import (
    TemplateUpdateTask,
    _rej_to_sidecar,
    restamp_base_template_provenance,
    run_template_update,
)
from forge.sync.forge_to_project.updater import update_project
from forge.sync.manifest import read_forge_toml, write_forge_toml
from forge.sync.template_version import resolve_template_version

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_minimal_project(
    tmp_path: Path,
    *,
    project_python_version: str = "1.0.0",
) -> Path:
    """Build a tiny forge-generated stub with one Python backend.

    Includes the forge.toml manifest plus the sentinel-bearing main.py
    so the updater can sentinel-audit + re-apply default-enabled
    fragments. ``project_python_version`` is the version stamped into
    ``[forge.template_versions]`` — set it equal to the live template
    version for no-delta tests, or different for delta tests.
    """
    root = tmp_path / "proj"
    backend = root / "services" / "backend"
    (backend / "src" / "app" / "core").mkdir(parents=True)
    (backend / "src" / "app" / "middleware").mkdir(parents=True)
    (backend / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0.1"\ndependencies = []\n',
        encoding="utf-8",
    )
    (backend / ".env.example").write_text("", encoding="utf-8")
    (backend / ".copier-answers.yml").write_text(
        "_src_path: /unused/in/tests\nproject_name: x\n", encoding="utf-8"
    )

    main_py = backend / "src" / "app" / "main.py"
    main_py.write_text(
        "\n".join(
            [
                "# FORGE:MIDDLEWARE_IMPORTS",
                "",
                "def create_app():",
                "    # FORGE:MIDDLEWARE_REGISTRATION",
                "    # FORGE:ROUTER_REGISTRATION",
                "    # FORGE:EXCEPTION_HANDLERS",
                "    # FORGE:APP_POST_CONFIGURE",
                "    return None",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (backend / "src" / "app" / "core" / "lifecycle.py").write_text(
        "def bootstrap():\n    # FORGE:LIFECYCLE_STARTUP\n    pass\n",
        encoding="utf-8",
    )
    write_forge_toml(
        root / "forge.toml",
        version="1.2.0",
        project_name="proj",
        templates={"python": "services/python-service-template"},
        options={},
        template_versions={"python": project_python_version},
    )
    return root


# ---------------------------------------------------------------------------
# 5a — template-version resolver
# ---------------------------------------------------------------------------


class TestTemplateVersionResolver:
    def test_resolves_from_toml_file(self, tmp_path: Path) -> None:
        template_root = tmp_path / "template"
        template_root.mkdir()
        (template_root / "_forge_template.toml").write_text(
            '[template]\nversion = "2.5.0"\n', encoding="utf-8"
        )
        assert resolve_template_version(template_root, spec_default="1.0.0") == "2.5.0"

    def test_template_version_resolution_prefers_toml_file_over_spec_default(
        self, tmp_path: Path
    ) -> None:
        """Toml file wins when both are present (test 7 from the plan)."""
        template_root = tmp_path / "template"
        template_root.mkdir()
        (template_root / "_forge_template.toml").write_text(
            '[template]\nversion = "1.5.0"\n', encoding="utf-8"
        )
        assert resolve_template_version(template_root, spec_default="1.0.0") == "1.5.0"

    def test_falls_back_to_spec_default_when_toml_missing(self, tmp_path: Path) -> None:
        template_root = tmp_path / "template"
        template_root.mkdir()
        assert resolve_template_version(template_root, spec_default="0.9.0") == "0.9.0"

    def test_falls_back_when_toml_malformed(self, tmp_path: Path) -> None:
        template_root = tmp_path / "template"
        template_root.mkdir()
        (template_root / "_forge_template.toml").write_text("not = valid =", encoding="utf-8")
        assert resolve_template_version(template_root, spec_default="0.7.0") == "0.7.0"

    def test_falls_back_when_template_section_absent(self, tmp_path: Path) -> None:
        template_root = tmp_path / "template"
        template_root.mkdir()
        (template_root / "_forge_template.toml").write_text("[other]\nx = 1\n", encoding="utf-8")
        assert resolve_template_version(template_root, spec_default="0.4.2") == "0.4.2"


# ---------------------------------------------------------------------------
# 5b — delta detection in update_project
# ---------------------------------------------------------------------------


class TestDeltaDetection:
    def test_no_version_delta_no_copier_call(self, tmp_path: Path) -> None:
        """Test 1 — versions match → no Copier call, behavior unchanged."""
        root = _make_minimal_project(tmp_path, project_python_version="1.0.0")
        with patch("forge.sync.forge_to_project.template_update.copier.run_update") as mock_run:
            summary = update_project(root, quiet=True)
        assert mock_run.call_count == 0
        # Backend was still walked, fragments still re-applied.
        assert summary["backends"] == ["backend"]
        assert summary["template_updates"] == []

    def test_version_bumped_triggers_copier_update(self, tmp_path: Path) -> None:
        """Test 2 — bump the template version → Copier called."""
        root = _make_minimal_project(tmp_path, project_python_version="0.5.0")
        with patch("forge.sync.forge_to_project.template_update.copier.run_update") as mock_run:
            summary = update_project(root, quiet=True)
        # Copier was called once for the python backend.
        assert mock_run.call_count == 1
        call = mock_run.call_args
        # Signature check: dst_path + the no-prompt args.
        assert call.kwargs["dst_path"].endswith("backend")
        assert call.kwargs["defaults"] is True
        assert call.kwargs["overwrite"] is False
        assert call.kwargs["skip_answered"] is True
        assert call.kwargs["conflict"] == "rej"
        # Summary surfaces the template update.
        tu = summary["template_updates"]
        assert len(tu) == 1
        assert tu[0]["language"] == "python"
        assert tu[0]["project_version"] == "0.5.0"
        assert tu[0]["current_version"] == "1.0.0"
        assert tu[0]["status"] == "applied"

    def test_no_template_update_flag_skips_copier(self, tmp_path: Path) -> None:
        """Test 4 — ``--no-template-update`` means no Copier call."""
        root = _make_minimal_project(tmp_path, project_python_version="0.5.0")
        with patch("forge.sync.forge_to_project.template_update.copier.run_update") as mock_run:
            summary = update_project(root, quiet=True, no_template_update=True)
        assert mock_run.call_count == 0
        assert summary["template_updates"] == []
        # Fragments still re-applied.
        assert summary["backends"] == ["backend"]

    def test_template_versions_restamp_post_update(self, tmp_path: Path) -> None:
        """Test 5 — after a successful update, ``[forge.template_versions]`` matches the live version."""
        root = _make_minimal_project(tmp_path, project_python_version="0.5.0")
        with patch("forge.sync.forge_to_project.template_update.copier.run_update"):
            update_project(root, quiet=True)
        data = read_forge_toml(root / "forge.toml")
        assert data.template_versions["python"] == "1.0.0"

    def test_copier_error_aborts_fragment_application(self, tmp_path: Path) -> None:
        """Test 6 — Copier raising aborts the update run; fragments NOT re-applied."""
        import copier.errors

        root = _make_minimal_project(tmp_path, project_python_version="0.5.0")
        backend_dir = root / "services" / "backend"
        # Capture a snapshot of main.py before the doomed update.
        main_py = backend_dir / "src" / "app" / "main.py"
        before = main_py.read_text(encoding="utf-8")

        def _raise(**_: Any) -> None:
            raise copier.errors.UnsafeTemplateError(["unsafe"])

        with (
            patch(
                "forge.sync.forge_to_project.template_update.copier.run_update",
                side_effect=_raise,
            ),
            pytest.raises(ProvenanceError, match="Copier re-render failed"),
        ):
            update_project(root, quiet=True)

        # No fragment re-apply means main.py is byte-identical to before.
        assert main_py.read_text(encoding="utf-8") == before


# ---------------------------------------------------------------------------
# 5c — .rej → .forge-merge sidecar conversion
# ---------------------------------------------------------------------------


class TestRejConversion:
    def test_user_modified_base_template_file_creates_sidecar(self, tmp_path: Path) -> None:
        """Test 3 — user-modified base-template file + version bump → .forge-merge sidecar."""
        root = _make_minimal_project(tmp_path, project_python_version="0.5.0")
        # Hand-modify a base-template file. Need a provenance entry
        # tagging it as base-template so the classifier picks it up.
        main_py = root / "services" / "backend" / "src" / "app" / "main.py"

        # Stamp a base-template provenance entry for main.py with the
        # ORIGINAL sha so the classifier sees a user-modified state once
        # we mutate it.
        from forge.sync.merge import sha256_of_file

        original_sha = sha256_of_file(main_py)
        data = read_forge_toml(root / "forge.toml")
        provenance = {
            "services/backend/src/app/main.py": {
                "origin": "base-template",
                "sha256": original_sha,
                "template_name": "services/python-service-template",
                "template_version": "0.5.0",
            },
        }
        write_forge_toml(
            root / "forge.toml",
            version=data.version,
            project_name=data.project_name,
            templates=data.templates,
            options=data.options,
            template_versions=data.template_versions,
            provenance=provenance,
        )

        # Now mutate the file. New SHA != recorded SHA → classifier
        # tags it ``user-modified``.
        main_py.write_text(
            main_py.read_text(encoding="utf-8") + "\n# user edit\n", encoding="utf-8"
        )

        with patch("forge.sync.forge_to_project.template_update.copier.run_update"):
            update_project(root, quiet=True)

        # A pre-flight sidecar was emitted next to main.py.
        sidecar = main_py.with_suffix(main_py.suffix + ".forge-merge")
        assert sidecar.is_file()
        assert "pre-surface" in sidecar.read_text(encoding="utf-8")

    def test_rej_converted_to_sidecar_on_post_process(self, tmp_path: Path) -> None:
        """Copier-emitted .rej → forge .forge-merge with header."""
        target = tmp_path / "main.py"
        target.write_text("on disk\n", encoding="utf-8")
        rej = target.with_suffix(target.suffix + ".rej")
        rej.write_text("rejected body\n", encoding="utf-8")

        sidecar = _rej_to_sidecar(rej)
        assert sidecar is not None
        body = sidecar.read_text(encoding="utf-8")
        assert "forge merge conflict" in body
        assert "rejected body" in body
        # .rej is removed once consumed.
        assert not rej.exists()

    def test_rej_merged_into_existing_sidecar(self, tmp_path: Path) -> None:
        """Pre-flight wrote a sidecar; Copier emitted .rej → append, don't overwrite."""
        target = tmp_path / "main.py"
        target.write_text("on disk\n", encoding="utf-8")
        sidecar = target.with_suffix(target.suffix + ".forge-merge")
        sidecar.write_text("# pre-surface\nuser edits go here\n", encoding="utf-8")
        rej = target.with_suffix(target.suffix + ".rej")
        rej.write_text("new template body\n", encoding="utf-8")

        result = _rej_to_sidecar(rej)
        assert result == sidecar
        body = sidecar.read_text(encoding="utf-8")
        assert "user edits go here" in body
        assert "new template body" in body
        assert "Copier-emitted .rej content follows" in body


# ---------------------------------------------------------------------------
# 5d — provenance re-stamp post-template-update
# ---------------------------------------------------------------------------


class TestProvenanceRestamp:
    def test_provenance_restamped_for_base_template_files(self, tmp_path: Path) -> None:
        """Test 8 — base-template file sha + template_version re-stamped; fragment file untouched."""
        target_dir = tmp_path / "services" / "backend"
        target_dir.mkdir(parents=True)
        # Two files under the target dir; one tagged base-template, the
        # other tagged fragment. After re-stamp, only the base-template
        # entry's sha + version should change.
        base_file = target_dir / "main.py"
        base_file.write_text("base content\n", encoding="utf-8")
        frag_file = target_dir / "frag.py"
        frag_file.write_text("frag content\n", encoding="utf-8")

        provenance: dict[str, dict[str, Any]] = {
            "services/backend/main.py": {
                "origin": "base-template",
                "sha256": "stale_base_sha",
                "template_name": "x",
                "template_version": "0.1.0",
            },
            "services/backend/frag.py": {
                "origin": "fragment",
                "sha256": "stale_frag_sha",
                "fragment_name": "frag",
                "fragment_version": "0.1.0",
            },
        }
        mutated = restamp_base_template_provenance(
            tmp_path,
            provenance=provenance,
            language="python",
            target_dir=target_dir,
            new_version="2.0.0",
        )
        assert mutated == 1
        # base entry: sha + template_version both bumped.
        assert provenance["services/backend/main.py"]["sha256"] != "stale_base_sha"
        assert provenance["services/backend/main.py"]["template_version"] == "2.0.0"
        # fragment entry: untouched.
        assert provenance["services/backend/frag.py"]["sha256"] == "stale_frag_sha"
        assert provenance["services/backend/frag.py"]["fragment_version"] == "0.1.0"

    def test_sibling_prefix_dir_not_restamped(self, tmp_path: Path) -> None:
        """Sibling backend whose path is a string-prefix of the target is NOT contained.

        ``services/api-admin`` must not be treated as inside
        ``services/api``: a component-boundary-blind ``startswith`` check
        wrongly restamps the sibling's provenance, corrupting cross-backend
        provenance on re-render.
        """
        from forge.sync.merge import sha256_of_file

        services = tmp_path / "services"
        target_dir = services / "api"
        target_dir.mkdir(parents=True)
        sibling_dir = services / "api-admin"
        sibling_dir.mkdir(parents=True)

        in_file = target_dir / "main.py"
        in_file.write_text("in content\n", encoding="utf-8")
        sibling_file = sibling_dir / "main.py"
        sibling_file.write_text("sibling content\n", encoding="utf-8")

        provenance: dict[str, dict[str, Any]] = {
            "services/api/main.py": {
                "origin": "base-template",
                "sha256": "stale_in_sha",
                "template_version": "0.1.0",
            },
            "services/api-admin/main.py": {
                "origin": "base-template",
                "sha256": "stale_sibling_sha",
                "template_version": "0.1.0",
            },
        }
        mutated = restamp_base_template_provenance(
            tmp_path,
            provenance=provenance,
            language="python",
            target_dir=target_dir,
            new_version="2.0.0",
        )
        # Only the file genuinely inside services/api is restamped.
        assert mutated == 1
        assert provenance["services/api/main.py"]["template_version"] == "2.0.0"
        # The sibling is outside the target subtree and must be untouched.
        assert provenance["services/api-admin/main.py"]["sha256"] == "stale_sibling_sha"
        assert provenance["services/api-admin/main.py"]["template_version"] == "0.1.0"

    def test_idempotent_when_sha_already_matches(self, tmp_path: Path) -> None:
        """Re-running restamp on unchanged content returns mutated=0."""
        from forge.sync.merge import sha256_of_file

        target_dir = tmp_path / "services" / "backend"
        target_dir.mkdir(parents=True)
        base_file = target_dir / "main.py"
        base_file.write_text("content\n", encoding="utf-8")
        good_sha = sha256_of_file(base_file)

        provenance: dict[str, dict[str, Any]] = {
            "services/backend/main.py": {
                "origin": "base-template",
                "sha256": good_sha,
                "template_version": "2.0.0",
            },
        }
        mutated = restamp_base_template_provenance(
            tmp_path,
            provenance=provenance,
            language="python",
            target_dir=target_dir,
            new_version="2.0.0",
        )
        assert mutated == 0


# ---------------------------------------------------------------------------
# Pre-flight sidecar path containment
# ---------------------------------------------------------------------------


class TestPresurfacePathContainment:
    def test_sibling_prefix_dir_not_presurfaced(self, tmp_path: Path) -> None:
        """A base-template file in a sibling prefix dir gets no pre-flight sidecar.

        ``services/api-admin`` is not inside ``services/api``; the
        pre-flight must skip its files so we don't write edit-trail
        sidecars into a backend the current Copier call never touches.
        """
        from forge.sync.forge_to_project.template_update import (
            _presurface_user_modified_sidecars,
        )

        services = tmp_path / "services"
        target_dir = services / "api"
        target_dir.mkdir(parents=True)
        sibling_dir = services / "api-admin"
        sibling_dir.mkdir(parents=True)

        (target_dir / "main.py").write_text("in content\n", encoding="utf-8")
        (sibling_dir / "main.py").write_text("sibling content\n", encoding="utf-8")

        written = _presurface_user_modified_sidecars(
            target_dir,
            ("services/api/main.py", "services/api-admin/main.py"),
            tmp_path,
        )
        names = {p.name for p in written}
        # The in-target file is surfaced; the sibling-prefix one is not.
        assert (target_dir / "main.py.forge-merge") in written
        assert not (sibling_dir / "main.py.forge-merge").exists()
        assert "main.py.forge-merge" in names
        assert len(written) == 1


# ---------------------------------------------------------------------------
# Direct wrapper tests
# ---------------------------------------------------------------------------


class TestRunTemplateUpdate:
    def test_returns_applied_when_no_rej_files(self, tmp_path: Path) -> None:
        """When Copier succeeds and emits no .rej, status is ``applied``."""
        target = tmp_path / "backend"
        target.mkdir()
        task = TemplateUpdateTask(
            language="python",
            project_version="0.5.0",
            current_version="1.0.0",
            target_dir=target,
            template_src=tmp_path / "template",
        )
        with patch("forge.sync.forge_to_project.template_update.copier.run_update"):
            outcome = run_template_update(task, project_root=tmp_path)
        assert outcome.status == "applied"
        assert outcome.rej_files == ()
        assert outcome.sidecar_files == ()

    def test_returns_conflict_when_rej_files_appear(self, tmp_path: Path) -> None:
        """When Copier leaves a .rej behind, status is ``conflict`` and a sidecar is produced."""
        target = tmp_path / "backend"
        target.mkdir()
        task = TemplateUpdateTask(
            language="python",
            project_version="0.5.0",
            current_version="1.0.0",
            target_dir=target,
            template_src=tmp_path / "template",
        )

        # Side-effect: write a .rej under target during the mocked call.
        def _fake_run(**_: Any) -> None:
            rej = target / "main.py.rej"
            rej.write_text("rejected content\n", encoding="utf-8")

        with patch(
            "forge.sync.forge_to_project.template_update.copier.run_update",
            side_effect=_fake_run,
        ):
            outcome = run_template_update(task, project_root=tmp_path)
        assert outcome.status == "conflict"
        assert len(outcome.rej_files) == 1
        assert len(outcome.sidecar_files) == 1
        # .rej removed, .forge-merge created.
        assert not outcome.rej_files[0].exists()
        assert outcome.sidecar_files[0].name == "main.py.forge-merge"

    def test_error_status_when_copier_raises(self, tmp_path: Path) -> None:
        import copier.errors

        target = tmp_path / "backend"
        target.mkdir()
        task = TemplateUpdateTask(
            language="python",
            project_version="0.5.0",
            current_version="1.0.0",
            target_dir=target,
            template_src=tmp_path / "template",
        )

        def _raise(**_: Any) -> None:
            raise copier.errors.UserMessageError("boom")

        with patch(
            "forge.sync.forge_to_project.template_update.copier.run_update",
            side_effect=_raise,
        ):
            outcome = run_template_update(task, project_root=tmp_path)
        assert outcome.status == "error"
        assert outcome.error_message is not None
        assert "boom" in outcome.error_message


# ---------------------------------------------------------------------------
# End-to-end against the real generator (one heavyweight test)
# ---------------------------------------------------------------------------


class TestEndToEndAgainstGenerator:
    """End-to-end: real generator emits a project with template_versions; update sees no delta."""

    def test_generate_then_update_no_copier_call(self, tmp_path: Path) -> None:
        from forge.config import FrontendConfig, FrontendFramework
        from tests._legacy_generation import generate

        cfg = ProjectConfig(
            project_name="copierupdate",
            backends=[
                BackendConfig(
                    name="backend",
                    project_name="copierupdate",
                    language=BackendLanguage.PYTHON,
                ),
            ],
            frontend=FrontendConfig(framework=FrontendFramework.NONE, project_name="copierupdate"),
            options={},
            output_dir=str(tmp_path),
        )
        project_root = generate(cfg, quiet=True)
        # forge.toml should now carry template_versions for python.
        data = read_forge_toml(project_root / "forge.toml")
        assert data.template_versions.get("python") == "1.0.0"

        with patch("forge.sync.forge_to_project.template_update.copier.run_update") as mock_run:
            update_project(project_root, quiet=True)
        assert mock_run.call_count == 0
        shutil.rmtree(project_root, ignore_errors=True)


# ---------------------------------------------------------------------------
# Two-stage (overlay) layouts — base template must also be re-rendered
# ---------------------------------------------------------------------------


class TestTwoStageBaseUpdate:
    """A two-stage layout records only the overlay ``_src_path`` in its
    ``.copier-answers.yml``; ``copier.run_update(dst_path=...)`` therefore
    re-renders only the overlay and silently skips the shared base. The
    updater must drive Copier against the *base* first, then the overlay,
    so base-template changes are not lost on ``forge --update``.
    """

    def _resolve_src_path(self, call_kwargs: dict[str, Any], dst: Path) -> str:
        """Read the effective ``_src_path`` a ``run_update`` call targets.

        Copier resolves the template source from the answers file under
        ``dst_path`` (the default ``.copier-answers.yml`` or whatever
        ``answers_file`` overrides it with). Mirror that resolution so the
        test can assert *which* template each Copier invocation updates.
        """
        import yaml

        answers_rel = call_kwargs.get("answers_file") or ".copier-answers.yml"
        answers_path = Path(call_kwargs["dst_path"]) / answers_rel
        data = yaml.safe_load(answers_path.read_text(encoding="utf-8"))
        return str(data["_src_path"])

    def test_base_then_overlay_both_updated(self, tmp_path: Path) -> None:
        """run_template_update re-renders the base *and* the overlay.

        RED: today only one ``copier.run_update`` call fires, and it
        targets the overlay ``_src_path`` recorded in
        ``.copier-answers.yml`` — the shared base is never updated.
        """
        target = tmp_path / "apps" / "frontend"
        target.mkdir(parents=True)
        base_src = tmp_path / "templates" / "apps" / "vue-frontend-template"
        overlay_src = tmp_path / "templates" / "layouts" / "vue" / "docs"
        base_src.mkdir(parents=True)
        overlay_src.mkdir(parents=True)

        # The on-disk answers file records ONLY the overlay src (this is
        # exactly what the two-stage generator stamps).
        (target / ".copier-answers.yml").write_text(
            f"_src_path: {overlay_src}\nproject_name: x\n",
            encoding="utf-8",
        )

        task = TemplateUpdateTask(
            language="vue",
            project_version="0.5.0",
            current_version="1.0.0",
            target_dir=target,
            template_src=overlay_src,
            base_template_src=base_src,
        )

        seen: list[str] = []

        def _record(**kwargs: Any) -> None:
            seen.append(self._resolve_src_path(kwargs, target))

        with patch(
            "forge.sync.forge_to_project.template_update.copier.run_update",
            side_effect=_record,
        ):
            outcome = run_template_update(task, project_root=tmp_path)

        # Both the base and the overlay were re-rendered.
        assert str(base_src) in seen, (
            f"base template was never re-rendered; Copier only saw {seen}"
        )
        assert str(overlay_src) in seen
        # Base must be updated before the overlay re-applies on top.
        assert seen.index(str(base_src)) < seen.index(str(overlay_src))
        assert outcome.status == "applied"
        # The on-disk answers file is left recording the overlay src (the
        # base-update scaffolding must not corrupt the persisted answers).
        import yaml

        persisted = yaml.safe_load(
            (target / ".copier-answers.yml").read_text(encoding="utf-8")
        )
        assert persisted["_src_path"] == str(overlay_src)

    def test_single_stage_unchanged(self, tmp_path: Path) -> None:
        """A self-contained (single-render) task still fires exactly once."""
        target = tmp_path / "backend"
        target.mkdir()
        (target / ".copier-answers.yml").write_text(
            "_src_path: /tmpl\nproject_name: x\n", encoding="utf-8"
        )
        task = TemplateUpdateTask(
            language="python",
            project_version="0.5.0",
            current_version="1.0.0",
            target_dir=target,
            template_src=tmp_path / "template",
        )
        with patch(
            "forge.sync.forge_to_project.template_update.copier.run_update"
        ) as mock_run:
            run_template_update(task, project_root=tmp_path)
        assert mock_run.call_count == 1
