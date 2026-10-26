"""`forge doctor` — check the local environment for everything forge needs.

Produces a structured ``DoctorReport`` listing each check, its status
(ok / warn / fail), and an actionable suggestion for anything that
failed. ``--json`` output is usable by CI.
"""

from __future__ import annotations

import json
import re
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

CheckStatus = Literal["ok", "warn", "fail"]


@dataclass
class CheckResult:
    """One doctor check — name, status, human-readable detail, optional fix hint."""

    name: str
    status: CheckStatus
    detail: str
    fix: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "fix": self.fix,
        }


@dataclass
class DoctorReport:
    """Aggregate result of every doctor check."""

    results: list[CheckResult] = field(default_factory=list)

    @property
    def worst(self) -> CheckStatus:
        if any(r.status == "fail" for r in self.results):
            return "fail"
        if any(r.status == "warn" for r in self.results):
            return "warn"
        return "ok"

    def as_dict(self) -> dict[str, Any]:
        return {
            "worst": self.worst,
            "checks": [r.as_dict() for r in self.results],
        }


# -- Individual checks --------------------------------------------------------


def check_tool_on_path(
    name: str, *, min_version: str | None = None, kind: str = "tool"
) -> CheckResult:
    """Check that ``name`` is on PATH. If ``min_version`` is given, verify
    the reported version meets it (string-compare, which is good enough for
    SemVer-ish tools).
    """
    resolved = shutil.which(name)
    if not resolved:
        return CheckResult(
            name=f"{kind}:{name}",
            status="fail",
            detail=f"{name!r} not found on PATH",
            fix=f"Install {name} and ensure it's available in your shell's PATH.",
        )
    if min_version is None:
        return CheckResult(
            name=f"{kind}:{name}",
            status="ok",
            detail=f"{name} found at {resolved}",
        )
    try:
        version_line = subprocess.run(
            [name, "--version"], capture_output=True, text=True, timeout=5, check=False
        ).stdout.strip()
    except Exception as e:  # noqa: BLE001
        return CheckResult(
            name=f"{kind}:{name}",
            status="warn",
            detail=f"{name} found but `--version` failed: {e}",
        )
    return CheckResult(
        name=f"{kind}:{name}",
        status="ok",
        detail=f"{name} {version_line}",
    )


# Minimum Python forge supports — keep in sync with ``requires-python`` in
# pyproject.toml (">=3.13").
MIN_PYTHON: tuple[int, int] = (3, 13)


def check_python_interpreter(min_version: tuple[int, int] = MIN_PYTHON) -> CheckResult:
    """Verify the running interpreter meets forge's minimum Python.

    forge can't import at all on an interpreter below the floor, so in
    practice this reports ``ok``; making it a real version comparison (vs.
    a hardcoded assertion) means a too-old interpreter surfaces a clear,
    actionable failure instead of an opaque import error upstream.
    """
    current = sys.version_info[:2]
    want = f"{min_version[0]}.{min_version[1]}"
    have = f"{current[0]}.{current[1]}"
    if current < min_version:
        return CheckResult(
            name="runtime:python",
            status="fail",
            detail=f"Python {have} at {sys.executable}; forge requires >= {want}",
            fix=f"Install Python {want}+ and run forge under it (e.g. via uv or pyenv).",
        )
    return CheckResult(
        name="runtime:python",
        status="ok",
        detail=f"Python {sys.version.split()[0]} at {sys.executable}",
    )


def check_docker_reachable() -> CheckResult:
    """Verify ``docker info`` returns cleanly — enough to boot compose later."""
    exe = shutil.which("docker")
    if not exe:
        return CheckResult(
            name="docker:daemon",
            status="warn",
            detail="docker CLI not on PATH — `forge new` without Docker still works but `--no-docker=false` generation will fail",
            fix="Install Docker Desktop or docker-cli.",
        )
    try:
        proc = subprocess.run([exe, "info"], capture_output=True, text=True, timeout=5, check=False)
    except Exception as e:  # noqa: BLE001
        return CheckResult(
            name="docker:daemon",
            status="fail",
            detail=f"`docker info` raised: {e}",
            fix="Start Docker Desktop or the docker service.",
        )
    if proc.returncode != 0:
        return CheckResult(
            name="docker:daemon",
            status="fail",
            detail=f"`docker info` failed (exit {proc.returncode}): {proc.stderr.strip()[:200]}",
            fix="Start Docker Desktop or the docker service, then re-run `forge doctor`.",
        )
    return CheckResult(
        name="docker:daemon",
        status="ok",
        detail="docker info succeeded",
    )


# ``develop.watch.initial_sync`` first shipped in Docker Compose 2.40.0 (ADR-013).
MIN_COMPOSE_FOR_DEV_SERVER = (2, 40, 0)


def parse_compose_version(output: str) -> tuple[int, ...] | None:
    """The numbers of ``docker compose version --short`` (``v2.40.3``, ``5.3.0``)."""
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", output)
    return tuple(int(part) for part in match.groups()) if match else None


def check_compose_for_dev_server(project_path: Path) -> CheckResult | None:
    """For a project that enables ``frontend.dev_server``, require a Compose that can watch.

    Returns ``None`` when the project does not use the overlay.
    """
    manifest = project_path / "forge.toml"
    if not manifest.is_file():
        return None
    try:
        from forge.sync.manifest import read_forge_toml  # noqa: PLC0415

        enabled = read_forge_toml(manifest).frontend.dev_server
    except Exception:  # noqa: BLE001
        return None  # check_forge_toml reports an unreadable manifest
    if not enabled:
        return None
    need = ".".join(str(n) for n in MIN_COMPOSE_FOR_DEV_SERVER)
    fix = f"Update Docker Desktop or the compose plugin to {need} or newer."
    exe = shutil.which("docker")
    if not exe:
        return CheckResult(
            name="docker:compose-dev-server",
            status="fail",
            detail="docker CLI not on PATH; docker-compose.dev.yaml needs Compose " + need,
            fix=fix,
        )
    try:
        proc = subprocess.run(
            [exe, "compose", "version", "--short"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception as e:  # noqa: BLE001
        return CheckResult(
            name="docker:compose-dev-server",
            status="fail",
            detail=f"`docker compose version` raised: {e}",
            fix=fix,
        )
    have = parse_compose_version(proc.stdout) if proc.returncode == 0 else None
    if have is None:
        return CheckResult(
            name="docker:compose-dev-server",
            status="fail",
            detail="could not read the Docker Compose version",
            fix=fix,
        )
    if have < MIN_COMPOSE_FOR_DEV_SERVER:
        return CheckResult(
            name="docker:compose-dev-server",
            status="fail",
            detail=(
                f"Docker Compose {'.'.join(map(str, have))}; docker-compose.dev.yaml needs {need}"
            ),
            fix=fix,
        )
    return CheckResult(
        name="docker:compose-dev-server",
        status="ok",
        detail=f"Docker Compose {'.'.join(map(str, have))} can watch (needs {need}+)",
    )


def check_port_free(port: int, *, host: str = "127.0.0.1") -> CheckResult:
    """Try to bind to ``host:port`` — if it fails, the port is in use."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1.0)
    try:
        sock.bind((host, port))
    except OSError as e:
        return CheckResult(
            name=f"port:{port}",
            status="warn",
            detail=f"port {port} is in use ({e})",
            fix=f"Stop the process using port {port}, or pass --backend-port to pick a different port.",
        )
    finally:
        sock.close()
    return CheckResult(
        name=f"port:{port}",
        status="ok",
        detail=f"port {port} is available",
    )


def check_registered_backends() -> list[CheckResult]:
    """Surface every backend in :data:`BACKEND_REGISTRY` as a doctor row.

    Built-ins emit three rows (Python / Node / Rust); each
    plugin-registered backend adds one. Each row reports the toolchain
    attached to the spec — which is how Epic S's plugin SDK guarantees
    that a plugin backend's install/verify steps are wired in.

    A plugin backend registered without attaching a toolchain (i.e.
    ``BackendSpec(...)`` with no ``toolchain`` argument) falls back to
    the :class:`NoopToolchain`, surfaced here as a ``warn`` so the user
    knows the plugin shipped with degraded support.
    """
    from forge.config import BACKEND_REGISTRY  # noqa: PLC0415
    from forge.toolchains import NoopToolchain  # noqa: PLC0415

    results: list[CheckResult] = []
    for lang, spec in BACKEND_REGISTRY.items():
        toolchain = spec.toolchain
        tc_name = getattr(toolchain, "name", "?")
        if isinstance(toolchain, NoopToolchain):
            results.append(
                CheckResult(
                    name=f"backend:{lang.value}",
                    status="warn",
                    detail=(
                        f"{spec.display_label} registered but its BackendSpec carries "
                        "the NoopToolchain — install/verify hooks will be skipped"
                    ),
                    fix=(
                        "If this is a plugin backend, attach a BackendToolchain to the "
                        "spec (see docs/adding-a-backend.md). Built-ins should never "
                        "land on Noop; file a bug at github.com/cchifor/forge."
                    ),
                )
            )
        else:
            results.append(
                CheckResult(
                    name=f"backend:{lang.value}",
                    status="ok",
                    detail=f"{spec.display_label} — toolchain: {tc_name}",
                )
            )
    return results


def check_ts_morph_toolchain() -> CheckResult:
    """P1.4 (1.1.0-alpha.2) — surface whether AST-based TS injection is wired.

    The Python regex anchor injector at ``forge/injectors/ts_ast.py`` is
    the default for ``.ts`` / ``.tsx`` / ``.js`` files. ``FORGE_TS_AST=1``
    opts into the ts-morph-backed sidecar, which is more durable under
    Prettier reformatting that moves anchor comments. The sidecar
    requires Node on PATH plus the ``ts-morph`` npm package reachable
    via ``NODE_PATH`` (or installed in the working directory's
    ``node_modules``).

    Doctor reports four states:

    * ``ok``   — Node + ts-morph reachable, ``FORGE_TS_AST`` set → AST
                 injection actively in use.
    * ``ok``   — Node + ts-morph reachable, env var unset → AST
                 injection available (one ``export FORGE_TS_AST=1`` away).
    * ``warn`` — Node missing, or Node present but ``ts-morph`` not
                 reachable. Forge still works; AST injection silently
                 falls back to regex.
    * ``warn`` — helper script missing (packaging regression — should
                 never trigger in a wheel install).
    """
    import os  # noqa: PLC0415

    from forge.injectors.ts_morph_sidecar import _HELPER_PATH  # noqa: PLC0415

    if not _HELPER_PATH.is_file():
        return CheckResult(
            name="ts-morph:helper",
            status="warn",
            detail=(
                f"ts-morph helper missing at {_HELPER_PATH} — "
                "AST injection unavailable, regex fallback in use"
            ),
            fix=("Re-install forge from a published wheel; the helper ships as package data."),
        )

    node = shutil.which("node")
    if node is None:
        return CheckResult(
            name="ts-morph:toolchain",
            status="warn",
            detail=(
                "Node not on PATH — ts-morph AST injection unavailable, "
                "TS injections use the regex anchor fallback"
            ),
            fix=(
                "Install Node 20+ and re-run `forge --doctor`. AST "
                "injection is opt-in via FORGE_TS_AST=1."
            ),
        )

    # Probe `ts-morph` reachability from this Node. Cheap: a require()
    # exits 0 when the module resolves and non-zero with a clean error
    # otherwise. NODE_PATH and any working-directory node_modules both
    # contribute to module resolution.
    try:
        proc = subprocess.run(
            [node, "-e", "require('ts-morph')"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception as e:  # noqa: BLE001
        return CheckResult(
            name="ts-morph:toolchain",
            status="warn",
            detail=f"ts-morph probe failed: {e}",
            fix="Install Node 20+ and `npm install ts-morph` in your project.",
        )

    if proc.returncode != 0:
        return CheckResult(
            name="ts-morph:toolchain",
            status="warn",
            detail=(
                "Node found but ts-morph not reachable — "
                "TS injections use the regex anchor fallback"
            ),
            fix=(
                "Install ts-morph: `npm install ts-morph` in the project "
                "root, or globally with `npm install -g ts-morph` and "
                "set NODE_PATH to the global modules dir."
            ),
        )

    # The helper must itself parse as valid JS. A syntax error (e.g. a
    # stray Python-ism leaking into a template literal) makes `node` exit
    # non-zero on *every* invocation, so AST injection silently degrades to
    # regex while ts-morph still looks "reachable". Probe it explicitly so
    # doctor surfaces the breakage instead of reporting a false "ok".
    try:
        check = subprocess.run(
            [node, "--check", str(_HELPER_PATH)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception:  # noqa: BLE001
        check = None
    if check is not None and check.returncode != 0:
        first_line = next((ln for ln in check.stderr.splitlines() if ln.strip()), "syntax error")
        return CheckResult(
            name="ts-morph:toolchain",
            status="warn",
            detail=(
                "ts-morph helper does not parse (`node --check` failed) — "
                f"AST injection unavailable, regex fallback in use: {first_line.strip()}"
            ),
            fix="Report this as a forge bug; the shipped helper should always parse.",
        )

    flag = os.getenv("FORGE_TS_AST", "").strip().lower()
    enabled = flag in ("1", "true", "yes", "on")
    state = "active (FORGE_TS_AST=1)" if enabled else ("available — set FORGE_TS_AST=1 to opt in")
    return CheckResult(
        name="ts-morph:toolchain",
        status="ok",
        detail=f"Node + ts-morph reachable; AST injection {state}",
    )


def check_forge_toml(project_path: Path) -> CheckResult:
    """If ``project_path/forge.toml`` exists, parse it and verify integrity."""
    manifest = project_path / "forge.toml"
    if not manifest.is_file():
        return CheckResult(
            name="forge.toml:presence",
            status="ok",
            detail=f"no forge.toml at {project_path} (not a forge-generated project)",
        )
    try:
        from forge.sync.manifest import read_forge_toml  # noqa: PLC0415

        data = read_forge_toml(manifest)
    except Exception as e:  # noqa: BLE001
        return CheckResult(
            name="forge.toml:integrity",
            status="fail",
            detail=f"cannot parse {manifest}: {e}",
            fix="Inspect forge.toml for syntax errors or restore from git.",
        )
    parts = [f"version={data.version}", f"project={data.project_name}"]
    if data.provenance:
        parts.append(f"provenance_entries={len(data.provenance)}")
    return CheckResult(
        name="forge.toml:integrity",
        status="ok",
        detail=", ".join(parts),
    )


def run(project_path: Path | None = None) -> DoctorReport:
    """Run every check and return the aggregate report."""
    project_path = project_path or Path.cwd()
    report = DoctorReport()

    # Python is required — verify the running interpreter meets forge's
    # minimum (a real version check, not a hardcoded assertion).
    report.results.append(check_python_interpreter())
    report.results.append(check_tool_on_path("git", kind="vcs"))
    report.results.append(check_tool_on_path("uv", kind="tool"))
    report.results.append(check_tool_on_path("node", kind="tool"))
    report.results.append(check_tool_on_path("npm", kind="tool"))
    report.results.append(check_tool_on_path("cargo", kind="tool"))
    report.results.append(check_tool_on_path("go", kind="tool"))
    report.results.append(check_tool_on_path("flutter", kind="tool"))
    report.results.append(check_docker_reachable())
    # P1.4 — surface ts-morph reachability so users planning to enable
    # FORGE_TS_AST=1 see whether the toolchain is ready.
    report.results.append(check_ts_morph_toolchain())

    # Surface every registered backend + its toolchain. Built-ins always
    # land in BACKEND_REGISTRY; plugin backends land here once loaded.
    report.results.extend(check_registered_backends())

    # Commonly used ports. Warnings, not failures — users can always pick
    # other ports via flags.
    for port in (5000, 5173, 18080):
        report.results.append(check_port_free(port))

    report.results.append(check_forge_toml(project_path))
    dev_server = check_compose_for_dev_server(project_path)
    if dev_server is not None:
        report.results.append(dev_server)
    return report


# -- CLI rendering ------------------------------------------------------------


_STATUS_GLYPH = {"ok": "[OK]", "warn": "[WARN]", "fail": "[FAIL]"}


def render_text(report: DoctorReport) -> str:
    lines: list[str] = []
    for r in report.results:
        glyph = _STATUS_GLYPH.get(r.status, "[?]")
        lines.append(f"  {glyph:<7} {r.name:<28} {r.detail}")
        if r.fix:
            lines.append(f"           -> fix: {r.fix}")
    worst_glyph = _STATUS_GLYPH[report.worst]
    lines.append("")
    lines.append(f"Overall: {worst_glyph}")
    return "\n".join(lines) + "\n"


def _dispatch_doctor(project_path: str = ".", *, json_output: bool = False) -> None:
    """CLI entry point for `forge doctor`."""
    report = run(Path(project_path))
    if json_output:
        sys.stdout.write(json.dumps(report.as_dict(), indent=2) + "\n")
    else:
        sys.stdout.write(render_text(report))
    sys.exit(0 if report.worst != "fail" else 1)
