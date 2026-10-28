"""Package integrity gate (Epic DD, 1.1.0-alpha.1).

Catches the two classes of regression that bite users in the field but
pass every other test:

1. **Missing template files in the shipped artefact.** Golden snapshots
   verify forge's generation output, but if ``uv build`` itself drops
   a file from the sdist/wheel, forge installs cleanly then fails at
   ``forge new`` with a cryptic copier "template not found" error. We
   smoke-check a handful of stable sentinel paths in both archives.
2. **Cache + build clutter leaking into the packaged artefact.**
   ``.ruff_cache/``, ``.pytest_cache/``, ``node_modules/``,
   ``.dart_tool/`` and similar can materialise in ``forge/templates/``
   during local dev and ship to PyPI unless MANIFEST.in excludes them.
   This test asserts none of those contaminants appear in either
   archive.

Gated behind ``@pytest.mark.package_integrity`` so the main pytest run
doesn't pay the ~6s ``uv build`` cost. CI runs it on ubuntu × 3.13 via
the ``package-integrity`` job. Developers can run it locally with
``pytest -m package_integrity``.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.package_integrity


# Sentinel files we expect to find in every built artefact. Drawn across
# the template tree so a "one backend dropped" regression fires on more
# than the most-exercised path. Update only when a fragment is renamed
# or removed intentionally — a failure here is usually a MANIFEST.in bug.
SENTINEL_TEMPLATE_FILES: tuple[str, ...] = (
    "forge/injectors/ts-morph-helper.mjs",
    "forge/templates/_common/quality.yml",
    "forge/templates/_common/skills/forge-platform/SKILL.md",
    "forge/templates/_common/skills/forge-platform/references/workloads.md",
    "forge/quality/architecture.py",
    "forge/quality/coverage.py",
    # Python service template
    "forge/templates/services/python-service-template/template/pyproject.toml.jinja",
    "forge/templates/services/python-service-template/template/src/app/main.py",
    # Node service template
    "forge/templates/services/node-service-template/template/package.json.jinja",
    # Rust service template
    "forge/templates/services/rust-service-template/template/Cargo.toml.jinja",
    # Vue frontend template
    "forge/templates/apps/vue-frontend-template/template/package.json.jinja",
    # Svelte frontend template
    "forge/templates/apps/svelte-frontend-template/template/package.json.jinja",
    # Flutter frontend template (keyed inside the Copier {{project_slug}} dir)
    "forge/templates/apps/flutter-frontend-template/{{project_slug}}/pubspec.yaml",
    # docker-compose template
    "forge/templates/deploy/docker-compose.yml.j2",
    # A cross-cutting fragment — the Python service's correlation_id impl.
    # Lives under forge/features/middleware/templates/ after the
    # features-reorganization refactor (the directory mirrors the
    # third-party plugin layout; see docs/plugin-development.md).
    "forge/features/middleware/templates/correlation_id/python/files/src/app/middleware/correlation.py",
)

# Anything matching one of these substrings in its path is a contaminant.
# Keep in sync with MANIFEST.in's recursive-exclude entries.
CONTAMINANT_SUBSTRINGS: tuple[str, ...] = (
    "/__pycache__/",
    "/.ruff_cache/",
    "/.mypy_cache/",
    "/.pytest_cache/",
    "/node_modules/",
    "/.venv/",
    "/htmlcov/",
    "/.next/",
    "/.svelte-kit/",
    "/.dart_tool/",
    # Cargo target/ from the auth Rust SDK's cross-SDK parity gate.
    # MANIFEST.in's recursive-exclude blocks these from sdist/wheel;
    # this assertion enforces the symmetry.
    "/target/",
    "/.coverage",
    "/dist/",
    # dist/build in templates would be stray local builds — safe to filter.
    # (We intentionally don't block top-level `build/` since setuptools
    # drops its build dir there; that's outside forge/templates.)
    "/.DS_Store",
)

CONTAMINANT_SUFFIXES: tuple[str, ...] = (
    ".pyc",
    ".pyo",
)


def _skip_if_no_uv() -> None:
    if shutil.which("uv") is None:
        pytest.skip("uv not on PATH; can't build artefacts for integrity check")


def _build_artefacts(tmp_path: Path) -> tuple[Path, Path]:
    """Run ``uv build`` into ``tmp_path/dist`` and return (sdist, wheel)."""
    _skip_if_no_uv()
    repo_root = Path(__file__).resolve().parent.parent
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()

    result = subprocess.run(
        ["uv", "build", "--out-dir", str(dist_dir)],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, (
        f"uv build failed:\n--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}"
    )

    # The PyPI distribution is ``forge-cli`` (RFC-003); build artifacts
    # normalize the hyphen to an underscore (``forge_cli-<ver>.{tar.gz,whl}``).
    # The importable package + console script stay ``forge``.
    sdists = list(dist_dir.glob("forge_cli-*.tar.gz"))
    wheels = list(dist_dir.glob("forge_cli-*.whl"))
    assert len(sdists) == 1, f"expected exactly one sdist, got {sdists}"
    assert len(wheels) == 1, f"expected exactly one wheel, got {wheels}"
    return sdists[0], wheels[0]


def _sdist_members(sdist: Path) -> list[str]:
    """Return every member path inside the sdist, normalised to POSIX."""
    with tarfile.open(sdist, "r:gz") as tar:
        return [m.name.replace("\\", "/") for m in tar.getmembers() if m.isfile()]


def _wheel_members(wheel: Path) -> list[str]:
    """Return every file member inside the wheel, normalised to POSIX."""
    with zipfile.ZipFile(wheel) as zf:
        return [name.replace("\\", "/") for name in zf.namelist() if not name.endswith("/")]


def _sdist_relative(member: str, prefix: str) -> str:
    """Strip the sdist root prefix (``forge-1.0.0/``) so paths match disk."""
    if member.startswith(prefix):
        return member[len(prefix) :]
    return member


# ---------------------------------------------------------------------------
# The integrity tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def artefacts(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """Build + cache the sdist and wheel for every test in this module."""
    return _build_artefacts(tmp_path_factory.mktemp("package-integrity"))


def test_installed_generator_fingerprint_matches_checkout(artefacts, tmp_path, monkeypatch):
    """A recipe generated from Git must survive installation of that same commit."""
    from forge.quality import model

    expected = model.source_fingerprint()
    with zipfile.ZipFile(artefacts[1]) as wheel:
        wheel.extractall(tmp_path)
    monkeypatch.setattr(model, "__file__", str(tmp_path / "forge/quality/model.py"))
    assert model.source_fingerprint() == expected


class TestSdist:
    def test_contains_every_sentinel_template_file(
        self, artefacts: tuple[Path, Path]
    ) -> None:
        sdist, _ = artefacts
        # sdist tar members are prefixed with `forge-<version>/`.
        members = _sdist_members(sdist)
        # Derive the prefix from the first member: the sdist root.
        prefix = members[0].split("/", 1)[0] + "/"
        rel = {_sdist_relative(m, prefix) for m in members}

        missing = [s for s in SENTINEL_TEMPLATE_FILES if s not in rel]
        assert not missing, (
            f"sdist is missing sentinel templates: {missing}. Check "
            f"MANIFEST.in `recursive-include forge/templates` + "
            f"pyproject.toml `[tool.setuptools.package-data]`."
        )

    def test_contains_no_contaminants(self, artefacts: tuple[Path, Path]) -> None:
        sdist, _ = artefacts
        members = _sdist_members(sdist)

        contaminated = [
            m
            for m in members
            if any(sub in m for sub in CONTAMINANT_SUBSTRINGS)
            or any(m.endswith(suf) for suf in CONTAMINANT_SUFFIXES)
        ]
        assert not contaminated, (
            f"sdist contains cache/build clutter ({len(contaminated)} entries). "
            f"First 10: {contaminated[:10]}. Check MANIFEST.in "
            f"recursive-exclude entries."
        )


class TestWheel:
    def test_contains_every_sentinel_template_file(
        self, artefacts: tuple[Path, Path]
    ) -> None:
        _, wheel = artefacts
        members = _wheel_members(wheel)
        # Wheel members are not prefixed with a root — they start at
        # `forge/` directly.
        members_set = set(members)

        missing = [s for s in SENTINEL_TEMPLATE_FILES if s not in members_set]
        assert not missing, (
            f"wheel is missing sentinel templates: {missing}. Check "
            f"pyproject.toml `[tool.setuptools.package-data] forge.templates "
            f"= [\"**/*\"]` and MANIFEST.in."
        )

    def test_contains_no_contaminants(self, artefacts: tuple[Path, Path]) -> None:
        _, wheel = artefacts
        members = _wheel_members(wheel)

        contaminated = [
            m
            for m in members
            if any(sub in m for sub in CONTAMINANT_SUBSTRINGS)
            or any(m.endswith(suf) for suf in CONTAMINANT_SUFFIXES)
        ]
        assert not contaminated, (
            f"wheel contains cache/build clutter ({len(contaminated)} entries). "
            f"First 10: {contaminated[:10]}. Check MANIFEST.in "
            f"recursive-exclude entries + pyproject.toml package-data glob."
        )

    def test_has_entry_point_for_forge_script(
        self, artefacts: tuple[Path, Path]
    ) -> None:
        """The wheel's entry-points metadata must expose the ``forge`` CLI.

        Catches a packaging-config regression that would make the
        installed distribution fail to register the `forge` console
        script — installable wheel but no `forge --version`.
        """
        _, wheel = artefacts
        with zipfile.ZipFile(wheel) as zf:
            entry_points_member = next(
                (n for n in zf.namelist() if n.endswith("entry_points.txt")),
                None,
            )
            assert entry_points_member is not None, (
                "wheel has no entry_points.txt — forge console script won't register"
            )
            body = zf.read(entry_points_member).decode("utf-8")
            assert "forge = forge.cli:main" in body, (
                f"wheel entry_points.txt is missing `forge = forge.cli:main`:\n{body}"
            )


class TestWhitelistSelfCheck:
    """Guardrail: make sure we're not missing whole classes of template files.

    If someone adds a ``.lua`` fragment to forge/templates/, the test author
    needs to know they should add that extension to the sentinel list (or
    decide it's fine without one). This self-check asserts the shipped
    extensions match a known whitelist; a new extension fails the test
    and forces an explicit decision.
    """

    KNOWN_TEMPLATE_EXTENSIONS: frozenset[str] = frozenset({
        # Code
        ".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
        ".dart", ".rs", ".vue", ".svelte", ".go",
        # Templating
        ".jinja", ".j2", ".mako",
        # Spec / contract (Pillar D.1 — TypeSpec port contracts under
        # forge/templates/_shared/ports/<port>/contract.tsp)
        ".tsp",
        # Config + data
        ".yaml", ".yml", ".json", ".toml", ".ini", ".env", ".properties",
        ".conf", ".lock", ".sql", ".sh", ".example",
        # Docs + markup
        ".md", ".rst", ".html", ".css", ".scss", ".typed",
        # Dockerfile bits + CI
        ".dockerignore", ".Dockerfile",
        # Licences
        ".MIT", ".APACHE2",
        # Intentional extensionless files live in EXTENSIONLESS_WHITELIST.
    })

    EXTENSIONLESS_WHITELIST: frozenset[str] = frozenset({
        "Dockerfile",
        ".dockerignore",
        ".editorconfig",
        ".gitignore",
        ".gitkeep",
        "LICENSE",
        "Makefile",
        "README",  # rare; mostly README.md
        # forge/templates/_common ships editor/git defaults without a
        # leading dot — common_files.py renames them on apply to the
        # conventional `.editorconfig` / `.gitignore` in the generated project.
        "editorconfig",
        "gitignore",
    })

    def test_templates_use_only_known_extensions(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        templates = repo_root / "forge" / "templates"

        unknown: list[tuple[str, str]] = []  # (path, extension-or-name)
        for path in templates.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(repo_root).as_posix()
            # Skip anything we'd exclude from sdist/wheel anyway — ruff
            # cache hashes + similar.
            if any(sub in f"/{rel}" for sub in CONTAMINANT_SUBSTRINGS):
                continue
            if any(rel.endswith(suf) for suf in CONTAMINANT_SUFFIXES):
                continue
            # Copier renders file *names* through Jinja; a file whose name is
            # wrapped in a ``{% if %}…{% endif %}`` block is emitted only when
            # the condition holds (e.g. ``{% if include_chat %}RightPanel.vue
            # {% endif %}``). Strip the statement blocks so the real extension
            # (``.vue``) is checked, not the trailing ``.vue{% endif %}``.
            eff_name = re.sub(r"\{%.*?%\}", "", path.name).strip()
            # Extensionless file?
            if "." not in eff_name:
                if eff_name not in self.EXTENSIONLESS_WHITELIST:
                    unknown.append((rel, f"(no ext: {eff_name})"))
                continue
            # Leading-dot files (.gitignore, .editorconfig) are matched
            # against the whitelist by full name since ``path.suffix``
            # returns the *last* dotted segment, not the leading dot name.
            if eff_name.startswith(".") and eff_name.count(".") == 1:
                if eff_name not in self.EXTENSIONLESS_WHITELIST:
                    unknown.append((rel, eff_name))
                continue
            ext = Path(eff_name).suffix
            if ext not in self.KNOWN_TEMPLATE_EXTENSIONS:
                unknown.append((rel, ext))

        assert not unknown, (
            f"Found {len(unknown)} template files with unrecognised names/extensions. "
            f"Either add them to KNOWN_TEMPLATE_EXTENSIONS/EXTENSIONLESS_WHITELIST "
            f"in tests/test_package_integrity.py (legitimate new file type) or "
            f"move them out of forge/templates/ (stray dev clutter). First 10:\n"
            + "\n".join(f"  {path}: {ext}" for path, ext in unknown[:10])
        )


def test_sentinel_files_exist_on_disk() -> None:
    """Every sentinel path lists a file that actually exists in the repo.

    Cheap sanity check — no build, just filesystem stat — so if a
    fragment is renamed or removed the sentinel list signals early.
    Still gated behind ``package_integrity`` because it lives with the
    rest of the suite; the whole module is opt-in via CI's dedicated
    ``package-integrity`` job.
    """
    repo_root = Path(__file__).resolve().parent.parent
    missing = [s for s in SENTINEL_TEMPLATE_FILES if not (repo_root / s).is_file()]
    assert not missing, (
        f"{len(missing)} sentinel paths don't exist on disk. Update "
        f"SENTINEL_TEMPLATE_FILES in tests/test_package_integrity.py "
        f"when templates are renamed. Missing:\n" + "\n".join(missing)
    )


# Expose the current interpreter on Windows where 'python' may resolve to
# a Microsoft Store stub — tests that need to subprocess an interpreter
# should `sys.executable` rather than 'python'.
_ = sys.executable
