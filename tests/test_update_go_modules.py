"""``forge --update`` resolves Go modules the way generation does."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest
import tomlkit

from forge.config import BackendConfig, BackendLanguage, ProjectConfig
from forge.errors import ForgeError
from forge.generator import generate
from forge.quality.model import digest
from forge.quality.update import update_owned_project
from forge.sync.manifest import read_forge_toml
from forge.toolchains import Check

MODULE = "services/orders"
GO_MOD = f"{MODULE}/go.mod"
GO_SUM = f"{MODULE}/go.sum"
TIDIED = "\nrequire example.com/indirect v1.0.0 // indirect\n"
UPSTREAM = "\nrequire example.com/upstream v1.2.3\n"


def _config(tmp_path: Path) -> ProjectConfig:
    return ProjectConfig(
        project_name="shop",
        output_dir=str(tmp_path),
        backends=[
            BackendConfig(
                name="orders", project_name="shop", language=BackendLanguage.GO, features=["items"]
            )
        ],
        frontends=[],
    )


def _fake_tidy(directory, cmd, description, *, quiet=False, required=False, **_kwargs) -> Check:
    assert cmd == ["go", "mod", "tidy"]
    module = Path(directory) / "go.mod"
    text = module.read_text(encoding="utf-8")
    if TIDIED not in text:
        text += TIDIED
        module.write_text(text, encoding="utf-8")
    (Path(directory) / "go.sum").write_text(
        hashlib.sha256(text.encode()).hexdigest() + "\n", encoding="utf-8"
    )
    return Check(name=description, status="ok")


@pytest.fixture
def fake_go(monkeypatch):
    real_which = shutil.which
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name, *a, **k: "/fake/go" if name == "go" else real_which(name, *a, **k),
    )
    monkeypatch.setattr("forge.toolchains.go.run_backend_cmd", _fake_tidy)
    monkeypatch.setattr("forge.quality.update.run_backend_cmd", _fake_tidy)
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")


@pytest.fixture
def project(tmp_path, fake_go) -> Path:
    return generate(_config(tmp_path / "out"), quiet=True)


def _upstream_go_mod_change(monkeypatch) -> None:
    real_generate = generate

    def changed(*args, **kwargs):
        candidate = real_generate(*args, **kwargs)
        module = candidate / GO_MOD
        module.write_text(module.read_text(encoding="utf-8") + UPSTREAM, encoding="utf-8")
        return candidate

    monkeypatch.setattr("forge.generator.generate", changed)


def _record(project: Path, rel: str) -> dict | None:
    return read_forge_toml(project / "forge.toml").provenance.get(rel)


def _rewrite_record(project: Path, rel: str, sha: str | None) -> None:
    manifest = tomlkit.parse((project / "forge.toml").read_text(encoding="utf-8"))
    if sha is None:
        del manifest["forge"]["provenance"][rel]
    else:
        manifest["forge"]["provenance"][rel]["sha256"] = sha
    (project / "forge.toml").write_text(tomlkit.dumps(manifest), encoding="utf-8")


def test_generation_records_the_tidied_module(project):
    assert (project / GO_MOD).read_text(encoding="utf-8").endswith(TIDIED)
    assert _record(project, GO_MOD)["sha256"] == digest(project / GO_MOD)
    assert _record(project, GO_SUM)["sha256"] == digest(project / GO_SUM)


def test_noop_update_keeps_the_tidied_module_and_is_idempotent(project):
    go_mod, go_sum = (project / GO_MOD).read_bytes(), (project / GO_SUM).read_bytes()
    generated = (project / "forge.toml").read_bytes()
    assert update_owned_project(project)["passed"]
    assert (project / "forge.toml").read_bytes() == generated
    assert (project / GO_MOD).read_bytes() == go_mod
    assert (project / GO_SUM).read_bytes() == go_sum
    gosum_record = _record(project, GO_SUM)
    assert gosum_record["sha256"] == digest(project / GO_SUM)
    assert gosum_record["ownership"] == "scaffold"
    assert _record(project, GO_MOD)["sha256"] == digest(project / GO_MOD)
    manifest = (project / "forge.toml").read_bytes()
    assert update_owned_project(project)["passed"]
    assert (project / "forge.toml").read_bytes() == manifest
    assert (project / GO_MOD).read_bytes() == go_mod


def test_upstream_change_rewrites_an_untouched_module_and_its_lock(project, monkeypatch):
    (project / GO_SUM).write_text("stale\n", encoding="utf-8")
    _upstream_go_mod_change(monkeypatch)
    assert update_owned_project(project)["passed"]
    text = (project / GO_MOD).read_text(encoding="utf-8")
    assert UPSTREAM in text and text.endswith(TIDIED)
    assert (project / GO_SUM).read_text(encoding="utf-8") == (
        hashlib.sha256(text.encode()).hexdigest() + "\n"
    )
    assert _record(project, GO_MOD)["sha256"] == digest(project / GO_MOD)
    assert _record(project, GO_SUM)["sha256"] == digest(project / GO_SUM)


def test_edited_module_gets_a_tidied_sidecar_on_upstream_change(project, monkeypatch):
    local = (project / GO_MOD).read_text(encoding="utf-8") + "\n// local pin\n"
    (project / GO_MOD).write_text(local, encoding="utf-8")
    go_sum = (project / GO_SUM).read_bytes()
    manifest = (project / "forge.toml").read_bytes()
    _upstream_go_mod_change(monkeypatch)
    report = update_owned_project(project)
    assert report["conflicts"] == [GO_MOD]
    assert (project / GO_MOD).read_text(encoding="utf-8") == local
    proposal = (project / (GO_MOD + ".forge-merge")).read_text(encoding="utf-8")
    assert UPSTREAM in proposal and proposal.endswith(TIDIED)
    assert (project / GO_SUM).read_bytes() == go_sum
    assert not (project / (GO_SUM + ".forge-merge")).exists()
    assert (project / "forge.toml").read_bytes() == manifest


def test_edited_module_without_upstream_change_is_kept(project):
    local = (project / GO_MOD).read_text(encoding="utf-8") + "\n// local pin\n"
    (project / GO_MOD).write_text(local, encoding="utf-8")
    (project / GO_SUM).write_text("local lock\n", encoding="utf-8")
    report = update_owned_project(project)
    assert report["passed"] and not report["conflicts"]
    assert (project / GO_MOD).read_text(encoding="utf-8") == local
    assert (project / GO_SUM).read_text(encoding="utf-8") == "local lock\n"


def test_missing_lock_is_restored(project):
    go_sum = (project / GO_SUM).read_bytes()
    (project / GO_MOD).write_text("module orders\n// local\n", encoding="utf-8")
    (project / GO_SUM).unlink()
    assert update_owned_project(project)["passed"]
    assert (project / GO_SUM).read_bytes() == go_sum


def test_user_owned_lock_is_preserved(project, monkeypatch):
    manifest = tomlkit.parse((project / "forge.toml").read_text(encoding="utf-8"))
    manifest["forge"]["provenance"][GO_SUM]["origin"] = "user"
    (project / "forge.toml").write_text(tomlkit.dumps(manifest), encoding="utf-8")
    (project / GO_SUM).write_text("user lock\n", encoding="utf-8")
    _upstream_go_mod_change(monkeypatch)
    assert update_owned_project(project)["passed"]
    assert UPSTREAM in (project / GO_MOD).read_text(encoding="utf-8")
    assert (project / GO_SUM).read_text(encoding="utf-8") == "user lock\n"
    assert _record(project, GO_SUM)["origin"] == "user"


def _record_untidy_baseline(project: Path) -> str:
    """The state an update without module resolution left behind."""
    untidy = (project / GO_MOD).read_text(encoding="utf-8").removesuffix(TIDIED)
    (project / GO_MOD).write_text(untidy, encoding="utf-8")
    _rewrite_record(project, GO_MOD, digest(project / GO_MOD))
    _rewrite_record(project, GO_SUM, None)
    return untidy


def test_untidy_baseline_from_an_earlier_update_is_repaired(project):
    go_mod, go_sum = (project / GO_MOD).read_bytes(), (project / GO_SUM).read_bytes()
    _record_untidy_baseline(project)
    (project / GO_SUM).write_text("stale\n", encoding="utf-8")
    assert update_owned_project(project)["passed"]
    assert (project / GO_MOD).read_bytes() == go_mod
    assert (project / GO_SUM).read_bytes() == go_sum
    assert _record(project, GO_SUM)["sha256"] == digest(project / GO_SUM)


def test_untidy_baseline_keeps_a_locally_resolved_module(project):
    untidy = _record_untidy_baseline(project)
    local = untidy + "\nrequire example.com/local v0.1.0\n"
    (project / GO_MOD).write_text(local, encoding="utf-8")
    (project / GO_SUM).write_text("local lock\n", encoding="utf-8")
    report = update_owned_project(project)
    assert report["passed"] and not report["conflicts"]
    assert (project / GO_MOD).read_text(encoding="utf-8") == local
    assert (project / GO_SUM).read_text(encoding="utf-8") == "local lock\n"
    tidied = hashlib.sha256((untidy + TIDIED).encode()).hexdigest()
    assert _record(project, GO_MOD)["sha256"] == tidied


def test_update_without_go_fails_before_writing(project, monkeypatch):
    real_which = shutil.which
    monkeypatch.setattr(
        shutil, "which", lambda name, *a, **k: None if name == "go" else real_which(name, *a, **k)
    )
    before = (project / "forge.toml").read_bytes()
    with pytest.raises(ValueError, match="install the go toolchain"):
        update_owned_project(project)
    assert (project / "forge.toml").read_bytes() == before


def test_failed_module_resolution_aborts_the_update(project, monkeypatch):
    def failing(directory, cmd, description, **_kwargs):
        raise ForgeError("Resolve go.sum failed (exit 1): go mod tidy")

    monkeypatch.setattr("forge.quality.update.run_backend_cmd", failing)
    before = (project / "forge.toml").read_bytes()
    go_mod = (project / GO_MOD).read_bytes()
    with pytest.raises(ValueError, match=f"Go modules of {MODULE}"):
        update_owned_project(project)
    assert (project / "forge.toml").read_bytes() == before
    assert (project / GO_MOD).read_bytes() == go_mod


@pytest.mark.skipif(shutil.which("go") is None, reason="go toolchain not on PATH")
def test_update_keeps_a_real_generated_module_tidy(tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")
    project = generate(_config(tmp_path), quiet=True)
    go_mod, go_sum = (project / GO_MOD).read_bytes(), (project / GO_SUM).read_bytes()
    assert b"// indirect" in go_mod
    generated = (project / "forge.toml").read_bytes()
    assert update_owned_project(project)["passed"]
    assert (project / "forge.toml").read_bytes() == generated
    assert (project / GO_MOD).read_bytes() == go_mod
    assert (project / GO_SUM).read_bytes() == go_sum
    assert update_owned_project(project)["passed"]
    assert (project / "forge.toml").read_bytes() == generated
    tidy = subprocess.run(
        ["go", "mod", "tidy", "-diff"], cwd=project / MODULE, capture_output=True, text=True
    )
    assert tidy.returncode == 0, tidy.stdout + tidy.stderr
