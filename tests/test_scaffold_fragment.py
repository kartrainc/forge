"""Tests for ``forge --plugins scaffold-fragment``.

Covers the contract surface plugin authors depend on:

* the command lays down the expected file tree;
* the generated ``inject.yaml`` files parse cleanly via the same
  ``yaml.safe_load`` path the plan validator uses;
* the generated ``fragments.py`` is syntactically valid Python
  (``ast.parse`` clean);
* re-running on a populated directory refuses to clobber without
  ``--force``;
* ``--name`` validation rejects identifiers that wouldn't survive being
  embedded in generated source.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest
import yaml

from forge.cli.commands.plugins import (
    _DEFAULT_BACKENDS,
    _dispatch_plugins,
    _parse_backends,
    _scaffold_fragment,
    _validate_fragment_name,
)

# ---------------------------------------------------------------------------
# _validate_fragment_name
# ---------------------------------------------------------------------------


class TestValidateFragmentName:
    def test_accepts_simple_identifier(self) -> None:
        _validate_fragment_name("my_fragment")

    def test_accepts_single_word(self) -> None:
        _validate_fragment_name("audit")

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            _validate_fragment_name("")

    def test_rejects_hyphen(self) -> None:
        with pytest.raises(ValueError, match="not a valid Python identifier"):
            _validate_fragment_name("my-fragment")

    def test_rejects_leading_digit(self) -> None:
        with pytest.raises(ValueError, match="not a valid Python identifier"):
            _validate_fragment_name("1fragment")

    def test_rejects_dot(self) -> None:
        with pytest.raises(ValueError, match="not a valid Python identifier"):
            _validate_fragment_name("ns.fragment")

    def test_rejects_python_keyword(self) -> None:
        with pytest.raises(ValueError, match="reserved word"):
            _validate_fragment_name("class")


# ---------------------------------------------------------------------------
# _parse_backends
# ---------------------------------------------------------------------------


class TestParseBackends:
    def test_default_when_none(self) -> None:
        assert _parse_backends(None) == _DEFAULT_BACKENDS

    def test_default_when_empty(self) -> None:
        assert _parse_backends("") == _DEFAULT_BACKENDS

    def test_subset(self) -> None:
        assert _parse_backends("python") == ("python",)

    def test_preserves_user_order(self) -> None:
        assert _parse_backends("rust,python") == ("rust", "python")

    def test_dedupes(self) -> None:
        assert _parse_backends("python,python,node") == ("python", "node")

    def test_rejects_unknown(self) -> None:
        with pytest.raises(SystemExit) as exc:
            _parse_backends("python,brainfuck")
        assert exc.value.code == 2


# ---------------------------------------------------------------------------
# _scaffold_fragment — happy path
# ---------------------------------------------------------------------------


def _expected_files(name: str, backends: tuple[str, ...]) -> set[str]:
    """Posix-style relative paths the scaffold should emit."""
    files = {
        "README.md",
        "fragments.py",
        "inject.yaml",
    }
    for backend in backends:
        if backend == "python":
            files.add(f"{backend}/files/__init__.py")
        elif backend == "node":
            files.add(f"{backend}/files/__init__.ts")
        elif backend == "rust":
            files.add(f"{backend}/files/lib.rs")
        elif backend == "go":
            files.add(f"{backend}/files/doc.go")
        files.add(f"{backend}/inject.yaml")
    return files


class TestScaffoldFragment:
    def test_writes_expected_tree_all_backends(self, tmp_path: Path) -> None:
        target = tmp_path / "out"
        _scaffold_fragment(
            name="my_fragment",
            output_dir=str(target),
            backends=_DEFAULT_BACKENDS,
            force=False,
        )
        actual = {p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()}
        assert actual == _expected_files("my_fragment", _DEFAULT_BACKENDS)

    def test_writes_subset_of_backends(self, tmp_path: Path) -> None:
        target = tmp_path / "out"
        _scaffold_fragment(
            name="my_fragment",
            output_dir=str(target),
            backends=("python",),
            force=False,
        )
        actual = {p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()}
        assert actual == _expected_files("my_fragment", ("python",))
        # Sibling backend trees must be absent — not just empty.
        assert not (target / "node").exists()
        assert not (target / "rust").exists()

    def test_inject_yaml_parses_cleanly(self, tmp_path: Path) -> None:
        target = tmp_path / "out"
        _scaffold_fragment(
            name="my_fragment",
            output_dir=str(target),
            backends=_DEFAULT_BACKENDS,
            force=False,
        )
        # Top-level placeholder + every per-backend inject.yaml.
        for path in [
            target / "inject.yaml",
            target / "python" / "inject.yaml",
            target / "node" / "inject.yaml",
            target / "rust" / "inject.yaml",
            target / "go" / "inject.yaml",
        ]:
            parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
            # The stubs are empty lists, which is the same shape the
            # plan validator accepts for a no-injection fragment.
            assert parsed == [], f"{path} parsed as {parsed!r}"

    def test_fragments_py_is_syntactically_valid(self, tmp_path: Path) -> None:
        target = tmp_path / "out"
        _scaffold_fragment(
            name="my_fragment",
            output_dir=str(target),
            backends=_DEFAULT_BACKENDS,
            force=False,
        )
        source = (target / "fragments.py").read_text(encoding="utf-8")
        # ast.parse raises SyntaxError on any malformed Python.
        ast.parse(source, filename=str(target / "fragments.py"))
        # And the rendered source must reference the fragment name + each
        # requested backend constant — proving the Jinja substitution
        # actually fired.
        assert 'name="my_fragment"' in source
        assert "BackendLanguage.PYTHON" in source
        assert "BackendLanguage.NODE" in source
        assert "BackendLanguage.RUST" in source
        assert "register_my_fragment" in source
        # TODO markers must survive — plugin authors rely on them.
        assert "TODO" in source

    def test_fragments_py_subset_only_emits_requested_backend(self, tmp_path: Path) -> None:
        target = tmp_path / "out"
        _scaffold_fragment(
            name="solo",
            output_dir=str(target),
            backends=("python",),
            force=False,
        )
        source = (target / "fragments.py").read_text(encoding="utf-8")
        assert "BackendLanguage.PYTHON" in source
        assert "BackendLanguage.NODE" not in source
        assert "BackendLanguage.RUST" not in source

    def test_readme_mentions_fragment_name(self, tmp_path: Path) -> None:
        target = tmp_path / "out"
        _scaffold_fragment(
            name="audit_log",
            output_dir=str(target),
            backends=_DEFAULT_BACKENDS,
            force=False,
        )
        readme = (target / "README.md").read_text(encoding="utf-8")
        assert "audit_log" in readme

    def test_jinja_suffix_is_stripped(self, tmp_path: Path) -> None:
        target = tmp_path / "out"
        _scaffold_fragment(
            name="x_frag",
            output_dir=str(target),
            backends=("python",),
            force=False,
        )
        # No file should retain the ``.jinja`` extension on disk.
        leftovers = [p for p in target.rglob("*.jinja")]
        assert leftovers == []


# ---------------------------------------------------------------------------
# Idempotency / --force
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_refuses_to_overwrite_without_force(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        target = tmp_path / "out"
        _scaffold_fragment(
            name="my_fragment",
            output_dir=str(target),
            backends=_DEFAULT_BACKENDS,
            force=False,
        )
        capsys.readouterr()  # drain the first-run output

        with pytest.raises(SystemExit) as exc:
            _scaffold_fragment(
                name="my_fragment",
                output_dir=str(target),
                backends=_DEFAULT_BACKENDS,
                force=False,
            )
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "refusing to overwrite" in err
        assert "--force" in err

    def test_force_clears_and_rerenders(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        target = tmp_path / "out"
        _scaffold_fragment(
            name="frag_a",
            output_dir=str(target),
            backends=_DEFAULT_BACKENDS,
            force=False,
        )
        # Drop a sentinel file that should NOT survive a --force re-render.
        sentinel = target / "stale.txt"
        sentinel.write_text("stale", encoding="utf-8")
        assert sentinel.exists()
        capsys.readouterr()

        _scaffold_fragment(
            name="frag_b",
            output_dir=str(target),
            backends=_DEFAULT_BACKENDS,
            force=True,
        )
        assert not sentinel.exists()
        # And the new name must be reflected in the rendered fragments.py.
        source = (target / "fragments.py").read_text(encoding="utf-8")
        assert 'name="frag_b"' in source
        assert "frag_a" not in source

    def test_empty_existing_directory_is_not_a_clobber(self, tmp_path: Path) -> None:
        """An existing-but-empty target is treated as a fresh render —
        the user almost certainly just `mkdir`'d the destination."""
        target = tmp_path / "out"
        target.mkdir()
        _scaffold_fragment(
            name="my_fragment",
            output_dir=str(target),
            backends=("python",),
            force=False,
        )
        assert (target / "fragments.py").exists()

    def test_output_dir_is_existing_file_exits_with_typed_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Codex Phase B round 1 follow-up: if the user passes an
        existing FILE path as --output-dir, we surface a controlled
        CLI error (exit 2) rather than letting `Path.iterdir()` raise
        `NotADirectoryError` uncontrolled.
        """
        target = tmp_path / "not_a_dir.txt"
        target.write_text("collision")
        with pytest.raises(SystemExit) as exc:
            _scaffold_fragment(
                name="my_fragment",
                output_dir=str(target),
                backends=("python",),
                force=False,
            )
        assert exc.value.code == 2
        assert "not a directory" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Dispatcher surface (the path the CLI parser actually reaches)
# ---------------------------------------------------------------------------


class TestDispatchScaffoldFragment:
    def test_missing_name_exits_with_usage_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as exc:
            _dispatch_plugins("scaffold-fragment", name=None)
        assert exc.value.code == 2
        assert "requires a fragment name" in capsys.readouterr().err

    def test_invalid_name_exits_with_usage_error(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        with pytest.raises(SystemExit) as exc:
            _dispatch_plugins(
                "scaffold-fragment",
                name="bad-name",
                output_dir=str(tmp_path / "out"),
            )
        assert exc.value.code == 2
        assert "not a valid Python identifier" in capsys.readouterr().err

    def test_happy_path_via_dispatch(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        target = tmp_path / "out"
        with pytest.raises(SystemExit) as exc:
            _dispatch_plugins(
                "scaffold-fragment",
                name="dispatch_demo",
                output_dir=str(target),
                backends="python",
            )
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "Scaffolded fragment 'dispatch_demo'" in out
        assert (target / "fragments.py").exists()
        # Generated source must still ast.parse — the dispatcher path
        # has no validation hop the direct path doesn't.
        ast.parse((target / "fragments.py").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Sanity check: the rendered fragments.py compiles in the same Python
# interpreter — picks up SyntaxWarning regressions ``ast.parse`` would
# miss (compile() runs additional checks like ``except*`` parsing).
# ---------------------------------------------------------------------------


def test_rendered_fragments_py_compiles(tmp_path: Path) -> None:
    target = tmp_path / "out"
    _scaffold_fragment(
        name="compiles_ok",
        output_dir=str(target),
        backends=_DEFAULT_BACKENDS,
        force=False,
    )
    source = (target / "fragments.py").read_text(encoding="utf-8")
    compiled = compile(source, str(target / "fragments.py"), "exec")
    assert compiled is not None
    # Smoke: this must be runnable under the current interpreter.
    assert sys.version_info >= (3, 11)
