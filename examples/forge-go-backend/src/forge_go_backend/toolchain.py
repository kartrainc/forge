"""``BackendToolchain`` implementation for the reference Go backend.

Mirrors the built-in toolchains' shape (install / verify / post_generate)
but talks only to forge's PUBLIC surface — ``forge.toolchains.Check`` — and
ships its own tiny subprocess runner so the example doesn't reach into
``forge``'s private helpers. Plugin authors can copy this file verbatim.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from time import perf_counter

from forge.toolchains import Check


def _run(backend_dir: Path, cmd: list[str], description: str, *, quiet: bool) -> Check:
    """Run ``cmd`` in ``backend_dir``; map the outcome to a :class:`Check`.

    Missing tool → ``skip`` (the toolchain isn't installed on this host);
    non-zero exit → ``fail``; success → ``ok``. Never raises — a generation
    must not abort because the optional verify step's tool is absent.
    """
    resolved = shutil.which(cmd[0])
    if resolved is not None:
        cmd = [resolved, *cmd[1:]]
    start = perf_counter()
    try:
        result = subprocess.run(
            cmd,
            cwd=str(backend_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
    except FileNotFoundError:
        duration_ms = int((perf_counter() - start) * 1000)
        if not quiet:
            print(f"  [!!] {description} skipped ({cmd[0]} not found)")
        return Check(
            name=description, status="skip", details=f"{cmd[0]} not found", duration_ms=duration_ms
        )
    except subprocess.TimeoutExpired:
        duration_ms = int((perf_counter() - start) * 1000)
        if not quiet:
            print(f"  [!!] {description} timed out")
        return Check(name=description, status="fail", details="timed out", duration_ms=duration_ms)

    duration_ms = int((perf_counter() - start) * 1000)
    if result.returncode == 0:
        if not quiet:
            print(f"  [ok] {description}")
        return Check(name=description, status="ok", details="", duration_ms=duration_ms)
    tail = "\n".join((result.stderr or result.stdout).strip().splitlines()[-5:])
    if not quiet:
        print(f"  [!!] {description} failed")
        for line in tail.splitlines():
            print(f"       {line}")
    return Check(
        name=description,
        status="fail",
        details=tail or f"exit {result.returncode}",
        duration_ms=duration_ms,
    )


class GoToolchain:
    """Install / verify hooks for a generated Go (net/http) service."""

    name = "go-stdlib"

    def install(self, backend_dir: Path, *, quiet: bool = False) -> None:
        # The reference service is standard-library-only, so ``go mod
        # download`` resolves nothing — but it primes the module cache for
        # forks that add real dependencies, and confirms the module graph
        # is well-formed. Best-effort: a missing ``go`` just skips.
        _run(backend_dir, ["go", "mod", "download"], "Download modules", quiet=quiet)

    def verify(self, backend_dir: Path, *, quiet: bool = False) -> list[Check]:
        return [
            _run(backend_dir, ["go", "vet", "./..."], "Vet", quiet=quiet),
            _run(backend_dir, ["go", "build", "./..."], "Build", quiet=quiet),
        ]

    def post_generate(self, backend_dir: Path, *, quiet: bool = False) -> None:
        # gofmt the rendered tree so a fork's first commit is canonically
        # formatted. Best-effort — absent ``gofmt`` is a no-op.
        _run(backend_dir, ["gofmt", "-w", "."], "Format", quiet=quiet)


GO_TOOLCHAIN = GoToolchain()
