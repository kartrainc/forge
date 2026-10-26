"""End-to-end test for the plugin contract (P0.2).

Exercises ``examples/forge-plugin-example/`` after it's been pip-installed
into the test environment. Catches drift between the public ``forge.api``
surface (``forge/api.py``) and the live plugin reference implementation:

* ``register()`` is callable and registers the option + fragment without
  raising.
* ``forge --plugins list`` enumerates the plugin in both text and JSON.
* The plugin's option appears in ``forge --list``/``--schema`` output —
  i.e. plugin-registered options are first-class in the resolver and CLI.
* Generation with ``--set example.hello_banner=true`` lays the
  fragment-authored file at the expected path and applies the injection.
* ``forge --update`` re-applies the plugin fragment idempotently.

CI-only marker: ``plugin_e2e``. The job at
``.github/workflows/plugin-e2e.yml`` runs this file with
``-m plugin_e2e`` after installing both forge and the example plugin
into the same virtualenv. Local developers can run it manually after
``pip install -e examples/forge-plugin-example``.
"""

from __future__ import annotations

import importlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.plugin_e2e


@pytest.fixture(scope="module")
def plugin_installed() -> None:
    """Skip the whole module unless the example plugin is importable.

    Installation is the workflow's responsibility (``pip install -e
    examples/forge-plugin-example``); we just refuse to run when the
    package isn't there so an unmarked local run doesn't false-fail.
    """
    try:
        importlib.import_module("forge_plugin_example")
    except ImportError:
        pytest.skip(
            "forge_plugin_example is not installed. "
            "Run `pip install -e examples/forge-plugin-example` first."
        )


@pytest.fixture
def loaded_plugin(plugin_installed: None):
    """Load plugins fresh into the live registries for each test.

    ``plugins.reset_for_tests`` only resets the bookkeeping
    (``LOADED_PLUGINS``, ``FAILED_PLUGINS``, ``COMMAND_REGISTRY``, plus
    unfreezing the fragment registry). It does **not** pop entries the
    plugin added to ``OPTION_REGISTRY`` / ``FRAGMENT_REGISTRY`` — those
    registries don't track which keys are plugin-owned. We do that
    explicitly here so re-running ``load_all`` doesn't trip the
    collision guard on the second pass.
    """
    from forge import plugins  # noqa: PLC0415
    from forge.fragments import FRAGMENT_REGISTRY  # noqa: PLC0415
    from forge.options import OPTION_REGISTRY  # noqa: PLC0415

    def _scrub_plugin_state() -> None:
        plugins.reset_for_tests()
        OPTION_REGISTRY.pop("example.hello_banner", None)
        if "example_hello_banner" in FRAGMENT_REGISTRY:
            del FRAGMENT_REGISTRY["example_hello_banner"]

    _scrub_plugin_state()
    plugins.load_all()
    yield
    _scrub_plugin_state()


# ---------------------------------------------------------------------------
# Plugin discovery + registration shape
# ---------------------------------------------------------------------------


class TestPluginDiscovery:
    """Verify the entry point is found and the registration callable runs."""

    def test_loaded_plugins_contains_example(self, loaded_plugin: None) -> None:
        from forge import plugins  # noqa: PLC0415

        names = [reg.name for reg in plugins.LOADED_PLUGINS]
        assert "example" in names, (
            f"forge_plugin_example not in LOADED_PLUGINS={names}; "
            f"FAILED_PLUGINS={plugins.FAILED_PLUGINS}"
        )

    def test_registration_counters_reflect_what_register_added(
        self, loaded_plugin: None
    ) -> None:
        from forge import plugins  # noqa: PLC0415

        reg = next(r for r in plugins.LOADED_PLUGINS if r.name == "example")
        # The reference plugin adds exactly one option + one fragment.
        # If we ever add a third, this test surfaces the change explicitly.
        assert reg.options_added == 1
        assert reg.fragments_added == 1

    def test_failed_plugins_list_is_empty_for_reference_plugin(
        self, loaded_plugin: None
    ) -> None:
        from forge import plugins  # noqa: PLC0415

        # The reference plugin is the contract. If it fails to register,
        # the entire plugin contract is broken.
        for name, error in plugins.FAILED_PLUGINS:
            if name == "example":
                pytest.fail(f"example plugin failed to register: {error}")

    def test_option_lands_in_OPTION_REGISTRY(self, loaded_plugin: None) -> None:
        from forge.options import OPTION_REGISTRY  # noqa: PLC0415

        assert "example.hello_banner" in OPTION_REGISTRY
        opt = OPTION_REGISTRY["example.hello_banner"]
        assert opt.default is False

    def test_fragment_lands_in_FRAGMENT_REGISTRY(
        self, loaded_plugin: None
    ) -> None:
        from forge.config import BackendLanguage  # noqa: PLC0415
        from forge.fragments import FRAGMENT_REGISTRY  # noqa: PLC0415

        assert "example_hello_banner" in FRAGMENT_REGISTRY
        frag = FRAGMENT_REGISTRY["example_hello_banner"]
        assert BackendLanguage.PYTHON in frag.implementations


# ---------------------------------------------------------------------------
# CLI introspection — `forge --plugins list`, `forge --list`, `forge --schema`
# ---------------------------------------------------------------------------


def _run_forge(*argv: str) -> subprocess.CompletedProcess[str]:
    """Run forge in a subprocess so the CLI dispatcher loads plugins from
    a clean process state. Returns the completed process for inspection."""
    return subprocess.run(
        [sys.executable, "-m", "forge", *argv],
        capture_output=True,
        text=True,
        check=False,
    )


class TestPluginCLI:
    """Plugin registrations show up in CLI output."""

    def test_plugins_list_text_mentions_example(
        self, plugin_installed: None
    ) -> None:
        result = _run_forge("--plugins", "list")
        assert result.returncode == 0, result.stderr
        assert "example" in result.stdout

    def test_plugins_list_json_includes_example(
        self, plugin_installed: None
    ) -> None:
        result = _run_forge("--plugins", "list", "--json")
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        loaded = payload["loaded"]
        names = [entry["name"] for entry in loaded]
        assert "example" in names
        # And the failed bucket is empty for the reference plugin.
        for entry in payload.get("failed", []):
            assert entry["name"] != "example"

    def test_option_visible_in_list(self, plugin_installed: None) -> None:
        result = _run_forge("--list", "--format", "json")
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        # ``--list --format json`` emits a top-level array of option dicts
        # (each carries ``name``, ``category``, ``stability``, …).
        assert isinstance(payload, list)
        names = [opt["name"] for opt in payload]
        assert "example.hello_banner" in names

    def test_option_appears_in_schema(self, plugin_installed: None) -> None:
        result = _run_forge("--schema")
        assert result.returncode == 0, result.stderr
        # The JSON Schema document key-on path; we just need to find it.
        assert "example.hello_banner" in result.stdout


# ---------------------------------------------------------------------------
# Generation with the plugin's option enabled
# ---------------------------------------------------------------------------


class TestPluginGeneration:
    """End-to-end: generate a Python project with the plugin option on."""

    def test_fragment_files_and_injection_land(
        self, loaded_plugin: None, tmp_path: Path
    ) -> None:
        from forge.config import (  # noqa: PLC0415
            BackendConfig,
            BackendLanguage,
            FrontendConfig,
            FrontendFramework,
            ProjectConfig,
        )
        from forge.generator import generate  # noqa: PLC0415

        cfg = ProjectConfig(
            project_name="plugin-e2e",
            backends=[
                BackendConfig(
                    name="backend",
                    project_name="plugin-e2e",
                    language=BackendLanguage.PYTHON,
                ),
            ],
            frontend=FrontendConfig(
                framework=FrontendFramework.NONE, project_name="plugin-e2e"
            ),
            options={"example.hello_banner": True},
            output_dir=str(tmp_path),
        )
        project_root = generate(cfg, quiet=True)
        try:
            # Fragment-authored file present at the expected path.
            hello = project_root / "services" / "backend" / "src" / "app" / "hello.py"
            assert hello.is_file()
            assert "print_banner" in hello.read_text(encoding="utf-8")

            # Injection landed: lifecycle.py imports + calls print_banner.
            lifecycle = (
                project_root / "services" / "backend" / "src" / "app" / "core" / "lifecycle.py"
            )
            body = lifecycle.read_text(encoding="utf-8")
            assert "from app.hello import print_banner" in body
            assert "print_banner()" in body

            # Provenance recorded the file as fragment-authored.
            from forge.sync.manifest import read_forge_toml  # noqa: PLC0415

            data = read_forge_toml(project_root / "forge.toml")
            rel = "services/backend/src/app/hello.py"
            assert rel in data.provenance
            assert data.provenance[rel]["origin"] == "fragment"
            assert (
                data.provenance[rel].get("fragment_name") == "example_hello_banner"
            )
        finally:
            shutil.rmtree(project_root, ignore_errors=True)


# ---------------------------------------------------------------------------
# Update flow with a plugin-registered option
# ---------------------------------------------------------------------------


class TestPluginUpdateFlow:
    """Plugin fragments survive a `forge --update` cycle."""

    def test_update_is_idempotent_for_plugin_fragment(
        self, loaded_plugin: None, tmp_path: Path
    ) -> None:
        from forge.config import (  # noqa: PLC0415
            BackendConfig,
            BackendLanguage,
            FrontendConfig,
            FrontendFramework,
            ProjectConfig,
        )
        from forge.generator import generate  # noqa: PLC0415
        from forge.sync.forge_to_project.updater import update_project  # noqa: PLC0415

        cfg = ProjectConfig(
            project_name="plugin-update",
            backends=[
                BackendConfig(
                    name="backend",
                    project_name="plugin-update",
                    language=BackendLanguage.PYTHON,
                ),
            ],
            frontend=FrontendConfig(
                framework=FrontendFramework.NONE, project_name="plugin-update"
            ),
            options={"example.hello_banner": True},
            output_dir=str(tmp_path),
        )
        project_root = generate(cfg, quiet=True)
        try:
            hello = project_root / "services" / "backend" / "src" / "app" / "hello.py"
            before = hello.read_text(encoding="utf-8")

            summary = update_project(project_root, quiet=True)

            after = hello.read_text(encoding="utf-8")
            # Fragment file untouched (skipped-idempotent under merge mode).
            assert before == after
            # Plugin's fragment shows up in the applied list.
            assert "example_hello_banner" in summary["fragments_applied"]
            # No conflicts on a clean re-apply.
            assert summary["file_conflicts"] == 0
        finally:
            shutil.rmtree(project_root, ignore_errors=True)
