"""Acceptance tests for source integrity, upgrade safety and strict coverage."""

from __future__ import annotations


import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from forge.config import BackendConfig, ProjectConfig
from forge.generator import generate
from forge.quality.architecture import boundary_violations, imports, verify_architecture
from forge.quality.coverage import changed_lines, evaluate, make_report, native_lines
from forge.quality.model import digest, ownership, project_path, read_recipe, subjects
from forge.quality.update import apply_transaction, migration_report, update_owned_project
from forge.recommend import RecommendationRequest, recommend


@pytest.fixture
def project():
    root = generate(
        ProjectConfig(project_name="gate-test", backends=[BackendConfig(name="api")]),
        quiet=True,
        dry_run=True,
    )
    yield root
    shutil.rmtree(root.parent)


def test_real_generation_integrity_and_hash_tampering(project):
    assert verify_architecture(project)["passed"]
    protected = next((project / "services/api/sdks/forge-core/src").rglob("*.py"))
    protected.write_text(protected.read_text(encoding="utf-8") + "\n# modified\n", encoding="utf-8")
    # Updating a local SHA does not authorize replacement of generic code.
    manifest = project / "forge.toml"
    from forge.sync.manifest import read_forge_toml

    previous = read_forge_toml(manifest).provenance[protected.relative_to(project).as_posix()][
        "sha256"
    ]
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(previous, digest(protected)), encoding="utf-8"
    )
    report = verify_architecture(project)
    assert not report["passed"]
    assert any("differs from regeneration" in message for message in report["violations"])


def test_added_shadow_source_rejected(project):
    shadow = project / "services/api/src/forge_core.py"
    shadow.write_text("pass\n", encoding="utf-8")
    assert any("unregistered" in p for p in verify_architecture(project)["violations"])


def test_custom_extension_survives_update_and_dry_run(project):
    extension = project / "services/api/src/app/custom/example.py"
    extension.parent.mkdir()
    extension.write_text("def business_rule():\n    return 42\n", encoding="utf-8")
    before = (project / "forge.toml").read_bytes()
    report = update_owned_project(project, dry_run=True)
    assert report["passed"]
    assert (project / "forge.toml").read_bytes() == before
    assert update_owned_project(project)["passed"]
    assert "return 42" in extension.read_text(encoding="utf-8")
    assert verify_architecture(project)["passed"]


def test_modified_protected_file_stops_update(project):
    path = next((project / "services/api/sdks/forge-core/src").rglob("*.py"))
    path.write_text("# application-specific override\n", encoding="utf-8")
    before = (project / "forge.toml").read_bytes()
    with pytest.raises(ValueError, match="Protected file edited"):
        update_owned_project(project)
    assert (project / "forge.toml").read_bytes() == before


def test_missing_protected_file_regenerated(project):
    path = next((project / "services/api/sdks/forge-core/src").rglob("*.py"))
    content = path.read_bytes()
    path.unlink()
    assert update_owned_project(project)["passed"]
    assert path.read_bytes() == content


_POSIX_MODES = pytest.mark.skipif(
    os.name == "nt", reason="Windows has no executable bit; chmod only toggles read-only"
)


@pytest.fixture
def two_database_project():
    """Two backends: Postgres needs ``deploy/compose/init-db.sh`` for the second database."""
    root = generate(
        ProjectConfig(
            project_name="init-db",
            backends=[
                BackendConfig(name="api"),
                BackendConfig(name="billing", server_port=5001),
            ],
        ),
        quiet=True,
        dry_run=True,
    )
    yield root
    shutil.rmtree(root.parent)


@_POSIX_MODES
def test_postgres_init_script_is_executable(two_database_project):
    # Postgres executes an executable init script and sources the rest, but
    # Docker Desktop reports a bind-mounted 0644 file as executable and then
    # refuses to run it, so the script must carry the bit itself.
    script = two_database_project / "deploy/compose/init-db.sh"
    assert script.read_bytes().startswith(b"#!/bin/bash\n")
    assert script.stat().st_mode & 0o777 == 0o755


@_POSIX_MODES
def test_update_makes_an_untouched_postgres_init_script_executable(two_database_project):
    # A project generated before the fix holds the script at 0644; as untouched
    # scaffold the update rewrites it with the regenerated file's mode.
    script = two_database_project / "deploy/compose/init-db.sh"
    content = script.read_bytes()
    script.chmod(0o644)
    assert update_owned_project(two_database_project)["passed"]
    assert script.read_bytes() == content
    assert script.stat().st_mode & 0o777 == 0o755


def test_recipe_pin_and_shared_inventory(project):
    recipe = json.loads((project / ".forge/quality.json").read_text(encoding="utf-8"))
    assert recipe["generator_requirement"].startswith("forge-cli")
    targets = subjects(read_recipe(project), project)
    assert any(item.get("shared") and item["language"] == "python" for item in targets)
    recipe["generator_sha256"] = "edited"
    (project / ".forge/quality.json").write_text(json.dumps(recipe), encoding="utf-8")
    with pytest.raises(ValueError, match="Generator differs"):
        read_recipe(project)
    assert read_recipe(project, check_generator=False).project_name == "gate-test"


@pytest.mark.parametrize("relative", ["../escape", "/tmp/escape", "a/../../escape", "a\\b"])
def test_path_escape_refused(tmp_path, relative):
    with pytest.raises(ValueError):
        project_path(tmp_path, relative)


def test_symlink_refused(tmp_path):
    (tmp_path / "link").symlink_to(tmp_path / "real")
    with pytest.raises(ValueError, match="Symlink"):
        project_path(tmp_path, "link/file.py")


def test_transaction_rollback(tmp_path, monkeypatch):
    (tmp_path / "one").write_bytes(b"before")
    original = Path.write_bytes

    def fail_second(path, data):
        if path.name == "two":
            raise OSError("simulated full disk")
        return original(path, data)

    monkeypatch.setattr(Path, "write_bytes", fail_second)
    with pytest.raises(OSError):
        apply_transaction(tmp_path, {"one": b"after", "two": b"created"})
    assert (tmp_path / "one").read_bytes() == b"before"
    assert not (tmp_path / "two").exists()


def test_transaction_delete_and_create(tmp_path):
    (tmp_path / "old").write_text("old", encoding="utf-8")
    apply_transaction(tmp_path, {"old": None, "new/nested": b"new"})
    assert not (tmp_path / "old").exists()
    assert (tmp_path / "new/nested").read_bytes() == b"new"


def test_ownership_policy():
    assert ownership("services/api/src/app/core/http.py") == "generated"
    assert ownership("services/api/src/app/custom/rules.py", "fragment") == "scaffold"
    assert ownership("services/api/tests/test_core.py", "fragment") == "scaffold"
    assert ownership("sdks/core/src/code.py", "user") == "user"
    assert ownership("services/api/src/app/services/item.py") == "scaffold"
    assert ownership("services/api/src/lib/errors.ts") == "generated"
    assert ownership("apps/web/src/shared/api/client.ts") == "generated"
    assert ownership("apps/web/src/shared/api/client.test.ts") == "scaffold"
    assert ownership("apps/web/src/features/items/services.ts") == "scaffold"
    assert (
        ownership("services/api/src/app/domain/canvas_events.py", template_name="_codegen")
        == "generated"
    )
    assert (
        ownership("services/api/src/app/domain/order.py", template_name="_domain_emitter")
        == "generated"
    )


def test_dependency_boundaries_and_monkey_patch(tmp_path):
    path = tmp_path / "custom.py"
    path.write_text(
        "import forge_core.secret as core\nfrom forge_core._private import key\ncore.method = None\n",
        encoding="utf-8",
    )
    problems = boundary_violations(tmp_path, {})
    assert any("private" in p for p in problems)
    assert any("replace generated" in p for p in problems)
    path.write_text("from app.custom import rules\n", encoding="utf-8")
    assert boundary_violations(tmp_path, {"custom.py": {"ownership": "generated"}})
    path.write_text("from app.ports import Store\n", encoding="utf-8")
    assert not boundary_violations(tmp_path, {})


@pytest.mark.parametrize(
    ("filename", "source"),
    [
        ("custom.py", "from forge_core import transport as core\ncore.send = None\n"),
        ("custom.py", "import forge_core as core\nsetattr(core, 'send', None)\n"),
        ("custom.py", "import forge_core as core\ndelattr(core, 'send')\n"),
        (
            "custom.ts",
            "import { Client as Core } from '@forge/core';\nCore.prototype.send = custom;\n",
        ),
    ],
)
def test_import_aliases_cannot_patch_generic_runtime(tmp_path, filename, source):
    (tmp_path / filename).write_text(source, encoding="utf-8")
    assert boundary_violations(tmp_path, {})


def test_local_rebinding_and_public_port_composition_are_allowed(tmp_path):
    (tmp_path / "custom.py").write_text(
        "from forge_core import transport\ntransport = None\nfrom app.ports import Store\n",
        encoding="utf-8",
    )
    assert boundary_violations(tmp_path, {}) == []


@pytest.mark.parametrize(
    ("filename", "body", "expected"),
    [
        ("test.py", "import os\nfrom app.ports import Store\n__import__('custom')", "custom"),
        ("test.ts", "const x = import('./custom/x');", "./custom/x"),
        ("test.rs", "use crate::custom::Store;", "crate::custom::Store"),
        (
            "test.dart",
            "import 'package:generated/_private.dart';",
            "package:generated/_private.dart",
        ),
    ],
)
def test_import_extraction(tmp_path, filename, body, expected):
    path = tmp_path / filename
    path.write_text(body, encoding="utf-8")
    assert expected in imports(path)


@pytest.fixture
def sources(tmp_path):
    source = tmp_path / "services/api/src/work.py"
    source.parent.mkdir(parents=True)
    source.write_text("\n".join(f"value{i} = {i}" for i in range(10)) + "\n", encoding="utf-8")
    return tmp_path, source


def reports_for(root, source, covered=9):
    relative = source.relative_to(root).as_posix()
    return [
        {
            "schema_version": 1,
            "subject": "api",
            "suite": suite,
            "tests": 1,
            "files": {
                relative: {
                    "sha256": digest(source),
                    "lines": {i: int(i <= covered) for i in range(1, 11)},
                }
            },
        }
        for suite in ("unit", "integration", "e2e")
    ]


TARGETS = [{"name": "api", "path": "services/api", "language": "python"}]


@pytest.mark.parametrize(("covered", "passed"), [(0, False), (8, False), (9, True), (10, True)])
def test_strict_greater_than_eighty(sources, covered, passed):
    root, source = sources
    result = evaluate(root, reports_for(root, source, covered), TARGETS)
    assert result["passed"] is passed
    assert result["subjects"][0]["new_total"] == 10


def test_union_does_not_average_or_double_count(sources):
    root, source = sources
    reports = reports_for(root, source, 5)
    for line in range(6, 11):
        next(iter(reports[1]["files"].values()))["lines"][line] = 1
    result = evaluate(root, reports, TARGETS)
    assert result["passed"]
    assert result["subjects"][0]["covered"] == 10
    assert result["subjects"][0]["total"] == 10


def test_missing_empty_duplicate_stale_and_omitted_reports_fail(sources):
    root, source = sources
    assert not evaluate(root, [], TARGETS)["passed"]
    reports = reports_for(root, source)
    assert not evaluate(root, reports[:-1], TARGETS)["passed"]
    assert not evaluate(root, [*reports, reports[0]], TARGETS)["passed"]
    reports[0]["tests"] = 0
    assert not evaluate(root, reports, TARGETS)["passed"]
    source.write_text(source.read_text(encoding="utf-8") + "# change\n", encoding="utf-8")
    assert not evaluate(root, reports, TARGETS)["passed"]
    reports = reports_for(root, source)
    (source.parent / "untested.py").write_text("value = 1\n", encoding="utf-8")
    assert not evaluate(root, reports, TARGETS)["passed"]


def test_report_escape_and_invalid_counter_fail(sources):
    root, source = sources
    reports = reports_for(root, source)
    next(iter(reports[0]["files"].values()))["lines"][10000] = 1
    with pytest.raises(ValueError, match="counter"):
        evaluate(root, reports, TARGETS)


def git(root, *args):
    return subprocess.run(
        ["git", *args], cwd=root, check=True, text=True, encoding="utf-8", capture_output=True
    ).stdout.strip()


def test_diff_coverage_and_missing_base(sources):
    root, source = sources
    git(root, "init")
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-m",
        "baseline",
    )
    base = git(root, "rev-parse", "HEAD")
    source.write_text(
        source.read_text(encoding="utf-8").replace("value9 = 9", "value9 = 99"), encoding="utf-8"
    )
    assert changed_lines(root, base)[source.relative_to(root).as_posix()] == {10}
    result = evaluate(root, reports_for(root, source, 9), TARGETS, base_ref=base)
    assert not result["passed"]
    assert result["subjects"][0]["new_total"] == 1
    with pytest.raises(subprocess.CalledProcessError):
        changed_lines(root, "nonexistent-base")
    (source.parent / "new.py").write_text("new = 1\n", encoding="utf-8")
    assert "services/api/src/new.py" in changed_lines(root, base)


def test_no_executable_changes_is_explicit_na(sources):
    root, source = sources
    git(root, "init")
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-m",
        "baseline",
    )
    result = evaluate(root, reports_for(root, source), TARGETS, base_ref="HEAD")
    assert result["passed"]
    assert result["subjects"][0]["new_total"] == 0


@pytest.mark.parametrize("name", ["café.py", "source with spaces.py"])
def test_diff_coverage_includes_quoted_paths(sources, name):
    root, source = sources
    source = source.rename(source.with_name(name))
    git(root, "init")
    git(root, "config", "core.quotepath", "true")
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-m",
        "baseline",
    )
    source.write_text(
        source.read_text(encoding="utf-8").replace("value9 = 9", "value9 = 99"),
        encoding="utf-8",
    )
    assert changed_lines(root, "HEAD")[source.relative_to(root).as_posix()] == {10}
    result = evaluate(root, reports_for(root, source, 9), TARGETS, base_ref="HEAD")
    assert not result["passed"]
    assert result["subjects"][0]["new_total"] == 1


@pytest.mark.parametrize("format", ["python", "istanbul", "lcov"])
def test_native_reports(sources, format):
    root, source = sources
    native = root / ("coverage.lcov" if format == "lcov" else "coverage.json")
    name = str(source)
    if format == "python":
        native.write_text(
            json.dumps(
                {"meta": {}, "files": {name: {"executed_lines": [1, 2], "missing_lines": [3]}}}
            ),
            encoding="utf-8",
        )
    elif format == "istanbul":
        native.write_text(
            json.dumps(
                {
                    name: {
                        "path": name,
                        "statementMap": {"0": {"start": {"line": 1}, "end": {"line": 5}}},
                        "s": {"0": 1},
                    }
                }
            ),
            encoding="utf-8",
        )
    else:
        native.write_text(f"SF:{name}\nDA:1,1\nDA:2,0\nend_of_record\n", encoding="utf-8")
    result = make_report(root, "api", "unit", native, source.parent, tests=1)
    assert result["files"][source.relative_to(root).as_posix()]["lines"][1] == 1
    with pytest.raises(ValueError):
        make_report(root, "api", "unit", native, source.parent, tests=0)


def test_empty_native_rejected(tmp_path):
    path = tmp_path / "empty.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        native_lines(path, tmp_path, tmp_path)


def test_shared_runtime_has_its_own_gate(sources):
    root, source = sources
    shared = root / "sdks/core/src/lib.py"
    shared.parent.mkdir(parents=True)
    shared.write_text("first = 1\nsecond = 2\n", encoding="utf-8")
    reports = reports_for(root, source, 10)
    for report in reports:
        report["files"][shared.relative_to(root).as_posix()] = {
            "sha256": digest(shared),
            "lines": {1: 1, 2: 0},
        }
    targets = TARGETS + [{"name": "core", "path": "sdks/core", "shared": True}]
    result = evaluate(root, reports, targets)
    assert not result["passed"]
    assert any("core: new-code coverage" in p for p in result["violations"])


API_CHANGE = {"services/api/src/work.py": set(range(1, 11))}


@pytest.fixture
def subset(monkeypatch):
    """evaluate with --coverage-subjects: a subset needs a base; ``changed`` is what the change touched."""

    def run(root, reports, targets, only, changed=None):
        delta = API_CHANGE if changed is None else changed
        monkeypatch.setattr("forge.quality.coverage.changed_lines", lambda root, base: delta)
        return evaluate(root, reports, targets, base_ref="main", only=only)

    return run


def _second_service(root):
    """A second backend, ``web``, with fully covered reports of its own."""
    source = root / "services/web/src/page.py"
    source.parent.mkdir(parents=True)
    source.write_text("\n".join(f"line{i} = {i}" for i in range(10)) + "\n", encoding="utf-8")
    reports = reports_for(root, source, 10)
    for report in reports:
        report["subject"] = "web"
    return reports, TARGETS + [{"name": "web", "path": "services/web", "language": "python"}]


def test_only_gates_the_named_subjects(sources, subset):
    root, source = sources
    _, targets = _second_service(root)
    reports = reports_for(root, source)  # api tested; web never ran
    full = evaluate(root, reports, targets)
    assert not full["passed"]  # the full gate still needs web
    assert set(full) == {"passed", "subjects", "violations"}  # its result keeps its shape
    result = subset(root, reports, targets, {"api"})
    assert result["passed"], result["violations"]
    assert [s["subject"] for s in result["subjects"]] == ["api"]
    assert result["skipped"] == ["web"]


def test_a_named_subject_is_gated_in_full(sources, subset):
    root, source = sources
    _, targets = _second_service(root)
    reports = reports_for(root, source)
    assert not subset(root, reports[:-1], targets, {"api"})["passed"]  # a missing suite
    assert not subset(root, reports_for(root, source, 8), targets, {"api"})["passed"]  # 80%
    (source.parent / "untested.py").write_text("value = 1\n", encoding="utf-8")
    result = subset(root, reports, targets, {"api"})
    assert any("source omitted" in v for v in result["violations"])


def test_only_ignores_the_reports_of_unnamed_subjects(sources, subset):
    root, source = sources
    web_reports, targets = _second_service(root)
    for report in web_reports:
        report["tests"] = 0  # malformed: would fail if web were gated
    result = subset(root, [*reports_for(root, source), *web_reports], targets, {"api"})
    assert result["passed"], result["violations"]


def test_only_gates_a_shared_package_a_named_subject_exercised(sources, subset):
    root, source = sources
    web_reports, targets = _second_service(root)
    shared = root / "sdks/core/src/lib.py"
    shared.parent.mkdir(parents=True)
    shared.write_text("first = 1\nsecond = 2\n", encoding="utf-8")
    targets = targets + [{"name": "core", "path": "sdks/core", "shared": True}]
    api_reports = reports_for(root, source, 10)
    for report in api_reports:
        report["files"][shared.relative_to(root).as_posix()] = {
            "sha256": digest(shared),
            "lines": {1: 1, 2: 0},
        }
    # api exercises core: core is gated (and fails its own bar on the changed lines)
    result = subset(
        root, api_reports, targets, {"api"}, {**API_CHANGE, "sdks/core/src/lib.py": {1, 2}}
    )
    assert any("core: new-code coverage" in v for v in result["violations"])
    # only web runs, and web never touches core: core is skipped, not failed
    result = subset(root, web_reports, targets, {"web"})
    assert result["passed"], result["violations"]
    assert sorted(result["skipped"]) == ["api", "core"]


def _two_consumers_of_core(root, source):
    """api covers core/src/a.py, web covers core/src/b.py: each file of the shared package by one consumer."""
    web_reports, targets = _second_service(root)
    targets = targets + [{"name": "core", "path": "sdks/core", "shared": True}]
    core = root / "sdks/core/src"
    core.mkdir(parents=True)
    for name in ("a", "b"):
        (core / f"{name}.py").write_text(f"{name} = 1\n", encoding="utf-8")
    api_reports = reports_for(root, source, 10)
    for reports, name in ((api_reports, "a"), (web_reports, "b")):
        for report in reports:
            report["files"][f"sdks/core/src/{name}.py"] = {
                "sha256": digest(core / f"{name}.py"),
                "lines": {1: 1},
            }
    return api_reports, web_reports, targets


def test_an_unnamed_shared_package_requires_only_its_changed_files(sources, monkeypatch):
    root, source = sources
    api_reports, _, targets = _two_consumers_of_core(root, source)
    # api alone ran: core/src/b.py (web's) has no report in this run
    monkeypatch.setattr("forge.quality.coverage.changed_lines", lambda root, base: {})
    result = evaluate(root, api_reports, targets, base_ref="main", only={"api"})
    assert result["passed"], result["violations"]
    # the change touched b.py: it must be covered by this run
    monkeypatch.setattr(
        "forge.quality.coverage.changed_lines", lambda root, base: {"sdks/core/src/b.py": {1}}
    )
    result = evaluate(root, api_reports, targets, base_ref="main", only={"api"})
    assert "core: source omitted from coverage: sdks/core/src/b.py" in result["violations"]


def test_every_report_present_feeds_a_shared_package(sources, subset):
    root, source = sources
    api_reports, web_reports, targets = _two_consumers_of_core(root, source)
    reports = [*api_reports, *web_reports]
    # web's reports are not gated, but they cover core's other file
    assert subset(root, reports, targets, {"api"})["passed"]
    # a shared package named alone is gated from its consumers' reports, in full
    result = subset(root, reports, targets, {"core"})
    assert result["passed"], result["violations"]
    assert [s["subject"] for s in result["subjects"]] == ["core"]
    assert not subset(root, api_reports, targets, {"core"})["passed"]  # b.py omitted


def test_a_changed_shared_package_is_gated_even_when_unexercised(sources, subset):
    root, source = sources
    web_reports, targets = _second_service(root)
    core = root / "sdks/core/src/lib.py"
    core.parent.mkdir(parents=True)
    core.write_text("first = 1\nsecond = 2\n", encoding="utf-8")
    targets = targets + [{"name": "core", "path": "sdks/core", "shared": True}]
    # the change edits core; web, the only subject run, never calls it
    result = subset(root, web_reports, targets, {"web"}, {"sdks/core/src/lib.py": {2}})
    assert not result["passed"]
    assert "core" not in result["skipped"]
    assert result["violations"] == [
        "core: the change touched sdks/core/src/lib.py, which no report of this run exercises; "
        "name a subject whose tests reach it"
    ]


def test_a_shared_package_whose_tests_alone_changed_is_not_forced(sources, subset):
    root, source = sources
    web_reports, targets = _second_service(root)
    (root / "sdks/core/src").mkdir(parents=True)
    (root / "sdks/core/src/lib.py").write_text("first = 1\n", encoding="utf-8")
    (root / "sdks/core/tests").mkdir()
    (root / "sdks/core/tests/test_lib.py").write_text("def test_it(): pass\n", encoding="utf-8")
    targets = targets + [{"name": "core", "path": "sdks/core", "shared": True}]
    # only core's tests changed: no gated source of core did, so web's run stays green
    result = subset(root, web_reports, targets, {"web"}, {"sdks/core/tests/test_lib.py": {1}})
    assert result["passed"], result["violations"]
    assert "core" in result["skipped"]


def test_a_changed_shared_package_is_gated_from_reports_present(sources, subset):
    root, source = sources
    api_reports, web_reports, targets = _two_consumers_of_core(root, source)
    # the change edits core's b.py; web's reports (present, not named) cover it
    result = subset(
        root, [*api_reports, *web_reports], targets, {"api"}, {"sdks/core/src/b.py": {1}}
    )
    assert result["passed"], result["violations"]
    assert "core" in [s["subject"] for s in result["subjects"]]


def test_a_subset_needs_a_base(sources):
    root, source = sources
    result = evaluate(root, reports_for(root, source), TARGETS, only={"api"})
    assert not result["passed"]
    assert any("needs --base-ref" in v for v in result["violations"])


def test_only_refuses_unknown_and_empty_selections(sources, subset):
    root, source = sources
    reports = reports_for(root, source)
    result = subset(root, reports, TARGETS, {"api", "apii"})
    assert "Unknown subject: apii" in result["violations"]
    assert "No coverage subjects selected" in subset(root, reports, TARGETS, set())["violations"]
    stray = {**reports[0], "subject": "ghost"}
    assert (
        "Unknown report subject: ghost"
        in subset(root, [*reports, stray], TARGETS, {"api"})["violations"]
    )


def test_migration_never_adopts_modified_source(project):
    source = next((project / "services/api/sdks/forge-core/src").rglob("*.py"))
    source.write_text("# customized\n", encoding="utf-8")
    proposal = migration_report(project)
    record = next(
        item for item in proposal["files"] if item["path"] == source.relative_to(project).as_posix()
    )
    assert record["ownership"] == "user"
    assert record["needs_review"]


def test_workload_recommendations_and_explicit_choice():
    request = RecommendationRequest.model_validate(
        {
            "services": [
                {"name": "processor", "workload": "processing"},
                {"name": "intelligence", "workload": "ai"},
                {"name": "notify", "workload": "notifications"},
                {"name": "custom", "workload": "crud", "language": "rust"},
            ]
        }
    )
    result = recommend(request)
    assert [d["language"] for d in result["decisions"]] == ["rust", "python", "node", "rust"]
    assert result["config"]
    assert result["decisions"][-1]["explicit"]


def test_library_constraint_and_remote_llm():
    result = recommend(
        RecommendationRequest.model_validate(
            {
                "existing_stack": "node",
                "services": [
                    {"name": "inference", "workload": "llm_api", "memory_mb": 512},
                    {
                        "name": "library",
                        "workload": "processing",
                        "required_library_languages": ["python"],
                    },
                ],
            }
        )
    )
    assert [d["language"] for d in result["decisions"]] == ["node", "python"]
    assert "benchmarks" in result["decisions"][0]["assumptions"][0]


def test_incompatible_explicit_choice_and_duplicate_names():
    with pytest.raises(ValueError):
        recommend(
            RecommendationRequest.model_validate(
                {
                    "services": [
                        {
                            "name": "ai",
                            "workload": "ai",
                            "language": "rust",
                            "required_library_languages": ["python"],
                        }
                    ]
                }
            )
        )
    with pytest.raises(ValueError, match="unique"):
        recommend(
            RecommendationRequest.model_validate(
                {
                    "services": [
                        {"name": "same", "workload": "ai"},
                        {"name": "same", "workload": "crud"},
                    ]
                }
            )
        )


def test_generator_fingerprint_ignores_runtime_coverage_files(tmp_path, monkeypatch):
    from forge.quality import model

    source = tmp_path / "forge"
    source.mkdir()
    runtime = source / "runtime.py"
    runtime.write_text("value = 1\n")
    monkeypatch.setattr(model, "__file__", str(source / "quality/model.py"))
    expected = model.source_fingerprint()
    (source / ".coverage").write_bytes(b"local coverage database")
    (source / ".coverage.worker.1").write_bytes(b"parallel worker database")
    assert model.source_fingerprint() == expected
    runtime.write_text("value = 2\n")
    assert model.source_fingerprint() != expected
