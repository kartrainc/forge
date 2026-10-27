"""Integration tests for ``forge --update`` against frontend-only projects.

Initiative #3 fixed three closely-related updater gaps:

* The early-bail at the top of ``_update_locked`` rejected any project
  without ``services/<backend>/`` dirs, breaking ``backend.mode=none``
  projects.
* ``apply_project_features`` was called without ``frontend_framework``,
  so :attr:`Fragment.target_frontends` gating silently no-op'd on
  update (a Vue-only fragment still ran against a Svelte / Flutter /
  frontend-less project).
* ``_collect_injection_targets`` passed ``options={}`` into
  ``FragmentPlan.from_impl``, so any option-rendered injection target
  path was never audited.

These tests assert both the positive (frontend-only update succeeds)
and negative (no-services + no-frontend still bails; v3 manifests
still load via inference) cases.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from forge.config import FrontendFramework
from forge.errors import ProvenanceError
from forge.sync.forge_to_project.updater import (
    _frontend_framework_from_manifest,
    _infer_backends,
    update_project,
)
from forge.sync.manifest import (
    CURRENT_SCHEMA_VERSION,
    ForgeFrontendData,
    read_forge_toml,
    write_forge_toml,
)

# ---------------------------------------------------------------------------
# Stubs — frontend-only project layout without going through the real
# generator / Copier. The updater reads what's on disk; we forge a
# minimal but loadable project the updater can re-stamp against.
# ---------------------------------------------------------------------------


def _stub_frontend_only_project(
    tmp_path: Path,
    *,
    framework: str = "vue",
    write_manifest_frontend: bool = True,
    schema_version: int | None = None,
) -> Path:
    """Build a stub frontend-only project at ``tmp_path / "proj"``.

    ``framework`` controls the recorded frontend (Vue / Svelte /
    Flutter). ``write_manifest_frontend`` toggles whether the manifest
    explicitly records ``[forge.frontend]`` (the v4 path) or omits it
    (the inference-fallback path).

    Produces:

    * ``apps/frontend/`` with a recognisable marker file (``package.json``
      + a framework-matching dep, or ``pubspec.yaml`` for Flutter).
    * ``forge.toml`` at the project root with no ``[forge.templates]``
      backend entries and the requested frontend table.

    Returns the project root path.
    """
    root = tmp_path / "proj"
    root.mkdir()
    app = root / "apps" / "frontend"
    app.mkdir(parents=True)
    if framework == "flutter":
        (app / "pubspec.yaml").write_text(
            "name: frontend\ndescription: stub\n",
            encoding="utf-8",
        )
    else:
        dep_key = "@sveltejs/kit" if framework == "svelte" else framework
        (app / "package.json").write_text(
            f'{{"name": "f", "dependencies": {{"{dep_key}": "^1.0.0"}}}}\n',
            encoding="utf-8",
        )

    manifest = root / "forge.toml"
    frontend_record = (
        ForgeFrontendData(framework=framework, app_dir="apps/frontend")
        if write_manifest_frontend
        else None
    )
    # Pass the frontend template_dir in [forge.templates] so the
    # write path stamps it (mirrors what the generator does).
    templates: dict[str, str] = {}
    if write_manifest_frontend:
        builtin_dirs = {
            "vue": "apps/vue-frontend-template",
            "svelte": "apps/svelte-frontend-template",
            "flutter": "apps/flutter-frontend-template",
        }
        if framework in builtin_dirs:
            templates[framework] = builtin_dirs[framework]
    kwargs = dict(
        version="1.2.0",
        project_name="frontend-only",
        templates=templates,
        options={"backend.mode": "none"},
        option_origins={"backend.mode": "user"},
        frontend=frontend_record,
    )
    if schema_version is not None:
        kwargs["schema_version"] = schema_version
    write_forge_toml(manifest, **kwargs)  # type: ignore[arg-type]
    return root


# ---------------------------------------------------------------------------
# Positive: frontend-only update succeeds, re-stamps to v4
# ---------------------------------------------------------------------------


class TestFrontendOnlyUpdatePositive:
    def test_vue_frontend_only_update_succeeds(self, tmp_path: Path) -> None:
        project_root = _stub_frontend_only_project(tmp_path, framework="vue")
        summary = update_project(project_root, quiet=True)
        # Backend list is empty (no services/), but the update succeeded.
        assert summary["backends"] == []
        assert summary["update_mode"] == "merge"
        # Manifest was re-stamped to current schema version.
        data = read_forge_toml(project_root / "forge.toml")
        assert data.schema_version == CURRENT_SCHEMA_VERSION
        assert data.frontend.framework == "vue"
        assert data.frontend.app_dir == "apps/frontend"

    def test_svelte_frontend_only_update_succeeds(self, tmp_path: Path) -> None:
        project_root = _stub_frontend_only_project(tmp_path, framework="svelte")
        summary = update_project(project_root, quiet=True)
        assert summary["backends"] == []
        data = read_forge_toml(project_root / "forge.toml")
        assert data.frontend.framework == "svelte"

    def test_flutter_frontend_only_update_succeeds(self, tmp_path: Path) -> None:
        project_root = _stub_frontend_only_project(tmp_path, framework="flutter")
        summary = update_project(project_root, quiet=True)
        assert summary["backends"] == []
        data = read_forge_toml(project_root / "forge.toml")
        assert data.frontend.framework == "flutter"

    def test_v3_manifest_upgrades_to_current_via_inference(self, tmp_path: Path) -> None:
        """A v3 manifest with apps/ on disk loads, updates, and stamps the current version."""
        # ``schema_version=3`` keeps the writer from emitting the v4
        # [forge.frontend] table; the read path then re-infers from
        # disk and the updater's re-stamp writes v4 with the
        # discovered framework.
        project_root = _stub_frontend_only_project(
            tmp_path,
            framework="vue",
            write_manifest_frontend=False,
            schema_version=3,
        )
        # Confirm precondition: the on-disk manifest is v3 and lacks
        # the [forge.frontend] table.
        before = (project_root / "forge.toml").read_text(encoding="utf-8")
        assert "schema_version = 3" in before
        assert "[forge.frontend]" not in before

        summary = update_project(project_root, quiet=True)
        assert summary["backends"] == []

        after = read_forge_toml(project_root / "forge.toml")
        assert after.schema_version == CURRENT_SCHEMA_VERSION
        # Inference picked up vue from apps/frontend/package.json.
        assert after.frontend.framework == "vue"
        assert after.frontend.app_dir == "apps/frontend"


# ---------------------------------------------------------------------------
# Negative: project with neither backends nor a discoverable frontend
# ---------------------------------------------------------------------------


class TestFrontendOnlyUpdateNegative:
    def test_no_backends_no_frontend_raises(self, tmp_path: Path) -> None:
        """Pure-empty project must still bail — no work to do."""
        root = tmp_path / "empty"
        root.mkdir()
        write_forge_toml(
            root / "forge.toml",
            version="1.2.0",
            project_name="empty",
            templates={},
            options={},
        )
        with pytest.raises(ProvenanceError, match="Nothing to update"):
            update_project(root, quiet=True)

    def test_explicit_framework_none_treated_as_no_frontend(self, tmp_path: Path) -> None:
        """A v4 manifest with ``[forge.frontend] framework = "none"``
        must be treated as "no frontend layer" — not as a synth-bridge
        trigger. Pinned because the bridge would otherwise pull in
        every Python project-scope fragment for a strictly empty
        project that the writer (e.g. a future migration) explicitly
        flagged ``framework = "none"`` for clarity.
        """
        root = tmp_path / "explicit_none"
        root.mkdir()
        write_forge_toml(
            root / "forge.toml",
            version="1.2.0",
            project_name="explicit-none",
            templates={},
            options={},
            frontend=ForgeFrontendData(framework="none", app_dir=""),
        )
        # No services/, no real frontend — should bail with "Nothing to update".
        with pytest.raises(ProvenanceError, match="Nothing to update"):
            update_project(root, quiet=True)

    def test_malformed_v3_manifest_falls_back_to_empty_frontend(self, tmp_path: Path) -> None:
        """A hand-rolled v3 with no apps/ scan target loads with empty frontend.

        Negative regression: the inference fallback never raises on a
        malformed-but-parseable v3 manifest — it falls through to
        ``ForgeFrontendData()`` and the updater treats the project as
        "no frontend known", same as backend-only.
        """
        root = tmp_path / "malformed"
        root.mkdir()
        (root / "forge.toml").write_text(
            dedent(
                """
                [forge]
                schema_version = 3
                version = "1.2.0"
                project_name = "malformed"

                [forge.templates]
                python = "services/python-service-template"

                [forge.options]

                [forge.option_origins]
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        # apps/frontend/ exists but the marker file is garbage — the
        # JSON parse fails, framework collapses to "".
        garbage = root / "apps" / "frontend"
        garbage.mkdir(parents=True)
        (garbage / "package.json").write_text("// not json at all\n", encoding="utf-8")

        data = read_forge_toml(root / "forge.toml")
        # Inference found apps/frontend/ but the parse failed; framework empty.
        assert data.frontend.framework == ""

        # No backends + no inferred frontend == bail with the same
        # ProvenanceError pre-Initiative-#3 callers got. The error
        # message changed to mention the frontend layer too, so the
        # match uses a regex that covers both legacy + new wording.
        with pytest.raises(ProvenanceError, match="Nothing to update"):
            update_project(root, quiet=True)


# ---------------------------------------------------------------------------
# Unit-level helpers: _frontend_framework_from_manifest, _infer_backends
# ---------------------------------------------------------------------------


class TestFrontendFrameworkFromManifest:
    def test_vue_record_maps_to_enum(self) -> None:
        fe = ForgeFrontendData(framework="vue", app_dir="apps/frontend")
        assert _frontend_framework_from_manifest(fe) == FrontendFramework.VUE

    def test_svelte_record_maps_to_enum(self) -> None:
        fe = ForgeFrontendData(framework="svelte", app_dir="apps/frontend")
        assert _frontend_framework_from_manifest(fe) == FrontendFramework.SVELTE

    def test_flutter_record_maps_to_enum(self) -> None:
        fe = ForgeFrontendData(framework="flutter", app_dir="apps/flutter_app")
        assert _frontend_framework_from_manifest(fe) == FrontendFramework.FLUTTER

    def test_empty_record_collapses_to_none(self) -> None:
        assert _frontend_framework_from_manifest(ForgeFrontendData()) == (FrontendFramework.NONE)

    def test_unknown_plugin_framework_collapses_to_none(self) -> None:
        """Plugin-registered frameworks we don't know about are treated as NONE.

        The updater's project-scope pass then skips ``target_frontends``-
        gated fragments — same effect as a no-frontend project, which is
        the conservative behaviour when forge can't introspect the
        framework.
        """
        fe = ForgeFrontendData(framework="solid", app_dir="apps/solid")
        assert _frontend_framework_from_manifest(fe) == FrontendFramework.NONE


class TestInferBackendsAcceptsManifestFrontend:
    """Forward-compat: ``_infer_backends`` accepts the manifest frontend.

    The argument is informational today — added so callers can pass
    the manifest record through without branching at the call site.
    Pinning the signature here keeps a future plugin-backend marker
    fallback from silently breaking the harvester / planner.
    """

    def test_accepts_manifest_frontend_kwarg(self, tmp_path: Path) -> None:
        fe = ForgeFrontendData(framework="vue", app_dir="apps/frontend")
        # Returns empty when services/ is missing — frontend record is
        # informational at this layer.
        assert _infer_backends(tmp_path, manifest_frontend=fe) == []

    def test_omitting_kwarg_is_equivalent(self, tmp_path: Path) -> None:
        assert _infer_backends(tmp_path) == []


# ---------------------------------------------------------------------------
# Resolver bridge: synth backend lets frontend-only updates run fragments
# ---------------------------------------------------------------------------


class TestResolverBridgeForFrontendOnly:
    """The resolver gates fragments on ``project_backends``. For a
    frontend-only project the updater synthesises a ``BackendLanguage.
    PYTHON`` placeholder so project-scope frontend fragments (which
    are registered under PYTHON purely for ``Fragment.implementations``
    non-emptiness) still reach :func:`apply_project_features` where
    ``target_frontends`` gating fires.

    These tests pin the bridge to the updater's summary so a future
    refactor that drops the synth backend resurfaces here — without
    requiring the resolver / capability_resolver to grow a frontend
    parameter (which is Init #7's territory).
    """

    def test_summary_does_not_leak_synth_backend(self, tmp_path: Path) -> None:
        """The synth ``_frontend_only`` BackendConfig is an
        implementation detail of the resolver bridge — the summary's
        ``backends`` list must not mention it. Pinned here so a future
        refactor doesn't accidentally surface internal scaffolding
        to JSON-mode callers.
        """
        project_root = _stub_frontend_only_project(tmp_path, framework="vue")
        summary = update_project(project_root, quiet=True)
        assert "_frontend_only" not in summary["backends"]
        assert summary["backends"] == []

    def test_manifest_restamp_does_not_leak_synth_backend(self, tmp_path: Path) -> None:
        """The re-stamped ``forge.toml`` must not record the synth
        backend's language (Python) in ``[forge.templates]``.

        Without the carve-out, every frontend-only update would
        silently add a Python service-template entry to a project
        that doesn't have one.
        """
        project_root = _stub_frontend_only_project(tmp_path, framework="vue")
        update_project(project_root, quiet=True)
        data = read_forge_toml(project_root / "forge.toml")
        # Vue frontend stays in templates; python should not appear
        # solely because of the synth bridge.
        assert "vue" in data.templates
        assert "python" not in data.templates

    def test_synth_bridge_narrows_to_frontend_targeted_only(self, tmp_path: Path) -> None:
        """Synth-bridge narrowing: when the only "backend" in play is
        the placeholder, project-scope fragments without
        ``target_frontends`` (e.g. ``platform_auth_sdk_python``,
        ``platform_auth_gatekeeper``) must NOT be applied. Pinned
        because the resolver's PYTHON-only filter would otherwise
        emit those into a project that has no Python backend.
        """
        root = tmp_path / "fe_only_auth"
        root.mkdir()
        app = root / "apps" / "frontend"
        app.mkdir(parents=True)
        (app / "package.json").write_text(
            '{"name": "f", "dependencies": {"vue": "^3.5.0"}}\n',
            encoding="utf-8",
        )
        write_forge_toml(
            root / "forge.toml",
            version="1.2.0",
            project_name="fe-auth",
            templates={"vue": "apps/vue-frontend-template"},
            options={"backend.mode": "none", "auth.mode": "generate"},
            option_origins={"backend.mode": "user", "auth.mode": "user"},
            frontend=ForgeFrontendData(framework="vue", app_dir="apps/frontend"),
        )

        # No services/ dir, no sdks/ dir — the apply pass must not
        # create either, despite ``auth.mode=generate`` pulling in
        # Python project-scope fragments at the resolver layer.
        update_project(root, quiet=True)
        assert not (root / "packages").exists(), (
            "synth bridge must NOT emit platform_auth_sdk_python files "
            "into a frontend-only project (no Python backend to host them)"
        )
        assert not (root / "deploy" / "infra" / "gatekeeper").exists(), (
            "synth bridge must NOT emit platform_auth_gatekeeper files into a frontend-only project"
        )

    def test_synth_bridge_pulls_in_frontend_targeted_fragments(self, tmp_path: Path) -> None:
        """Direct check at the resolver layer: with the synth backend,
        ``auth.mode=generate`` pulls every per-frontend session-timeout
        fragment into the plan. Without the bridge, the plan would be
        empty (covered by the inverse assertion below).

        This is the load-bearing invariant for Initiative #3: the
        manifest-only metadata flows through to a non-empty fragment
        plan when the project's only layer is the frontend.
        """
        from forge.capability_resolver import resolve  # noqa: PLC0415
        from forge.config import (  # noqa: PLC0415
            BackendConfig,
            BackendLanguage,
            FrontendConfig,
            ProjectConfig,
        )

        fc = FrontendConfig(framework=FrontendFramework.VUE, project_name="fe")
        # WITH synth backend — the bridge the updater installs.
        cfg_bridged = ProjectConfig(
            project_name="fe",
            backends=[
                BackendConfig(
                    name="_frontend_only",
                    project_name="fe",
                    language=BackendLanguage.PYTHON,
                ),
            ],
            frontends=[fc],
            # include_keycloak so auth.mode=generate stays effective (the
            # resolver coerces it to none when keycloak is off).
            include_keycloak=True,
            options={"backend.mode": "none", "auth.mode": "generate"},
            option_origins={"backend.mode": "user", "auth.mode": "user"},
        )
        bridged_plan = resolve(cfg_bridged)
        bridged_names = {rf.fragment.name for rf in bridged_plan.ordered}
        # Vue / Svelte / Flutter session-timeout fragments all light up
        # at the resolver layer — ``apply_project_features`` filters
        # them by ``target_frontends`` per the actual frontend choice.
        assert "platform_auth_session_timeout_vue" in bridged_names

        # WITHOUT synth backend — empty plan, regression confirmed.
        cfg_unbridged = ProjectConfig(
            project_name="fe",
            backends=[],
            frontends=[fc],
            options={"backend.mode": "none", "auth.mode": "generate"},
            option_origins={"backend.mode": "user", "auth.mode": "user"},
        )
        unbridged_plan = resolve(cfg_unbridged)
        assert unbridged_plan.ordered == ()


class TestPluginFrontendOnlyUpdate:
    """Plugin-registered frontends (a framework name forge doesn't
    know natively) still get the resolver-bridge treatment — the
    project is "frontend-only" from the manifest's perspective even
    when ``_frontend_framework_from_manifest`` collapses to
    ``FrontendFramework.NONE`` for the unknown name.
    """

    def test_plugin_frontend_only_update_does_not_raise(self, tmp_path: Path) -> None:
        # Write a manifest claiming a plugin framework "solid". No
        # apps/frontend/ on disk (the test scenario is "manifest has
        # the framework, project layout is whatever the plugin uses").
        root = tmp_path / "plugin_proj"
        root.mkdir()
        write_forge_toml(
            root / "forge.toml",
            version="1.2.0",
            project_name="plugin-fe",
            templates={},
            options={"backend.mode": "none"},
            option_origins={"backend.mode": "user"},
            frontend=ForgeFrontendData(framework="solid", app_dir="apps/solid-frontend"),
        )
        # Update should not raise "Nothing to update" — the manifest
        # records a frontend layer (plugin), even if we can't dispatch
        # ``target_frontends`` for it.
        summary = update_project(root, quiet=True)
        assert summary["backends"] == []
        # The manifest's frontend record carries through the re-stamp.
        after = read_forge_toml(root / "forge.toml")
        assert after.frontend.framework == "solid"
        assert after.frontend.app_dir == "apps/solid-frontend"
