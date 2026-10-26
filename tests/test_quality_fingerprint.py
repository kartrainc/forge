"""The generator fingerprint ignores Finder metadata, as the built package does.

macOS writes ``.DS_Store`` into any folder a person opens in Finder, including an
installed generator under a project's ``.cache``. The wheel excludes it
(``pyproject.toml``), so counting it made an untouched install fail the
"generator differs from recorded source" check.
"""

from __future__ import annotations

from pathlib import Path

from forge.quality import model


def _package(tmp_path: Path) -> Path:
    module = tmp_path / "forge" / "quality" / "model.py"
    module.parent.mkdir(parents=True)
    module.write_text("# stand-in\n", encoding="utf-8")
    (tmp_path / "forge" / "generator.py").write_text("x = 1\n", encoding="utf-8")
    return module


def test_fingerprint_ignores_finder_metadata(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(model, "__file__", str(_package(tmp_path)))
    before = model.source_fingerprint()

    (tmp_path / "forge" / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1")
    (tmp_path / "forge" / "quality" / ".DS_Store").write_bytes(b"\x00")

    assert model.source_fingerprint() == before


def test_fingerprint_still_sees_a_changed_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(model, "__file__", str(_package(tmp_path)))
    before = model.source_fingerprint()

    (tmp_path / "forge" / "generator.py").write_text("x = 2\n", encoding="utf-8")

    assert model.source_fingerprint() != before
