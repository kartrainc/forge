"""Go / net/http backend toolchain.

``install`` resolves ``go.sum`` with ``go mod tidy`` (the Go analogue of a
lockfile resolution) so frozen installs and image builds have a module graph
to verify. ``verify`` builds, checks formatting, vets, lints with the pinned
staticcheck (the analogue of ``cargo clippy -D warnings``) and runs the unit
suite; integration/E2E run through the quality gate with their database
fixtures.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from forge.toolchains import Check
from forge.toolchains._runner import run_backend_cmd

# Keep in sync with forge/templates/_common/ci_go.yml.
STATICCHECK = "honnef.co/go/tools/cmd/staticcheck@v0.8.2"
# forge --update tidies its candidate the same way, so both record the same go.mod/go.sum.
TIDY = ["go", "mod", "tidy"]


def _gofmt_check(backend_dir: Path, *, quiet: bool) -> Check:
    """``gofmt -l`` exits 0 even when files need formatting; its output decides."""
    gofmt = shutil.which("gofmt")
    if gofmt is None:
        return Check(name="Format check", status="skip", details="gofmt not found")
    result = subprocess.run(
        [gofmt, "-l", "."],
        cwd=backend_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    unformatted = result.stdout.strip()
    if result.returncode or unformatted:
        if not quiet:
            print("  [!!] Format check failed")
        return Check(name="Format check", status="fail", details=unformatted or result.stderr)
    if not quiet:
        print("  [ok] Format check")
    return Check(name="Format check", status="ok")


class GoToolchain:
    name = "go"

    def install(self, backend_dir: Path, *, quiet: bool = False) -> None:
        run_backend_cmd(backend_dir, TIDY, "Resolve go.sum", quiet=quiet)

    def verify(self, backend_dir: Path, *, quiet: bool = False) -> list[Check]:
        return [
            run_backend_cmd(backend_dir, ["go", "build", "./..."], "Build", quiet=quiet),
            _gofmt_check(backend_dir, quiet=quiet),
            run_backend_cmd(backend_dir, ["go", "vet", "./..."], "Vet", quiet=quiet),
            run_backend_cmd(backend_dir, ["go", "run", STATICCHECK, "./..."], "Lint", quiet=quiet),
            run_backend_cmd(
                backend_dir, ["go", "test", "-count=1", "./tests/unit/..."], "Tests", quiet=quiet
            ),
        ]

    def post_generate(self, backend_dir: Path, *, quiet: bool = False) -> None:
        run_backend_cmd(backend_dir, ["gofmt", "-w", "."], "Format", quiet=quiet)


GO_TOOLCHAIN = GoToolchain()
