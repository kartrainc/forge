"""Config-matrix validation runner (Epic S / sprint 1 deliverable).

Drives each scenario in ``tests/matrix/scenarios.yaml`` through one or
more lanes:

- **Lane A — generate**: invokes :func:`forge.generator.generate` into a
  fresh tempdir and asserts the expected-file manifest is present.
  Runs on every PR, ~1 min/scenario.
- **Lane B — toolchain verify**: sprint 2 deliverable (placeholder hook
  exists below). Invokes each backend's :class:`BackendToolchain.verify`
  plus the frontend build.
- **Lane C — compose-up smoke**: sprint 2 deliverable. Runs
  ``docker compose up``, waits on healthchecks, runs the RFC-006 HTTP
  contract, tears down.
- **Lane D — bidirectional round-trip** (Phase 5/6). For each opted-in
  scenario: generate → assert FR1 (zero block/files harvest candidates
  on the fresh project) → stage a synthetic literal-block edit →
  harvest → apply the bundle to the live forge tree (snapshotted +
  reverted in ``finally``) → regenerate → assert FR2 (project_a ==
  project_b modulo documented noise). ~30-90s per scenario, nightly
  only. See ``docs/round-trip.md`` for the contract.
- **Lane E — forge --update + --harvest e2e** (WS4). For each scenario,
  shells out to ``python -m forge --update --mode {merge,skip,overwrite}``
  against an edited project (per-mode contract validated) plus a single
  ``forge --harvest --harvest-out=-`` JSON-on-stdout probe. Exercises
  the argparse + builder + dispatcher path real users hit, complementing
  lane D's direct-Python-API round-trip. Nightly only; see
  ``tests/matrix/update_contract.py`` for the per-mode assertion details.

CLI usage (from the repo root)::

    uv run python tests/matrix/runner.py --all                  # all scenarios, lane A
    uv run python tests/matrix/runner.py --scenario py_vue_full
    uv run python tests/matrix/runner.py --list                 # list scenarios + lanes
    uv run python tests/matrix/runner.py --all --lane generate  # explicit lane
    uv run python tests/matrix/runner.py --scenario py_only_headless --lane roundtrip

Lane D (``roundtrip``) — Phase 5/6. Enforces FR1 (fresh-generate emits
zero block/files harvest candidates) AND FR2 (the full forward→reverse
→forward cycle reaches a project tree byte-equal to the user's edit,
modulo documented noise). Phase 6 wired the block apply-back surface
and the live-forge snapshot+restore guard so the second generate
re-emits the harvested edit. Runs nightly only via
``.github/workflows/matrix-nightly.yml``.

Exit codes::

    0 — all selected scenarios passed the requested lane
    2 — at least one scenario failed
    3 — runner misconfiguration (bad scenarios.yaml, missing forge)

Imports :mod:`forge.generator` directly rather than shelling out to the
``forge`` CLI. That's deliberate for lane A — it's faster, gives better
tracebacks, and bypasses the argparse / headless-detection code paths
the CLI suite covers. Lane C will shell out.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any, Literal

import yaml

Lane = Literal["generate", "verify", "smoke", "roundtrip", "update"]
ALL_LANES: tuple[Lane, ...] = ("generate", "verify", "smoke", "roundtrip", "update")

SCENARIOS_YAML = Path(__file__).parent / "scenarios.yaml"
WELD_STUBS_DIR = Path(__file__).parent / "fixtures" / "packages"


_INSTALL_TIMEOUT_SECONDS = 300
_BUILD_TIMEOUT_SECONDS = 180


@dataclass(frozen=True)
class _FrontendVerifyOutcome:
    """Tagged result of ``_verify_frontend`` (Initiative #9 — surface skips).

    * ``status="ok"`` — install + build both succeeded. ``error`` / ``skip_reason`` empty.
    * ``status="fail"`` — install or build returned a non-zero exit. ``error`` carries
      the label the caller embeds in the lane FAIL ``details``.
    * ``status="skip"`` — the check was bypassed (Flutter, no frontend, no JS runtime,
      ...). ``skip_reason`` names the missing dependency / opt-out for the
      annotation emitter.
    """

    status: Literal["ok", "fail", "skip"]
    error: str = ""
    skip_reason: str = ""


def _verify_frontend(fe_cfg, fe_dir: Path) -> _FrontendVerifyOutcome:
    """Run a build/analyze check against the generated frontend.

    Lane B's frontend complement to ``BackendToolchain.verify`` — runs
    ``<pm> install`` + ``<pm> run build`` in the generated
    ``apps/frontend/`` so vue-tsc / svelte-check / vite build regressions
    surface on PR rather than waiting for nightly compose-up (lane C).

    Returns a :class:`_FrontendVerifyOutcome` tagging the outcome as
    ``ok`` / ``fail`` / ``skip``. Initiative #9 split this from the
    pre-Init pattern (``None`` for both success AND skip) so silent
    skips (no npm on PATH, Flutter scenario, etc.) can be surfaced
    as GitHub Actions annotations rather than masquerading as
    coverage.

    Budget (worst-case wall-clock per scenario): install 5 min +
    build 3 min = 8 min. Lane B aims for ~5 min average across the
    matrix; the upper bound covers cold CI caches without letting a
    runaway process stall the whole runner.

    Skips (returned with ``skip_reason``) when:

    * The frontend framework is Flutter — ``subosito/flutter-action`` is
      heavy and lane B targets <5 min/scenario; Flutter is covered by
      ``tests/e2e/test_full_generation.py::test_flutter_*``.
    * No frontend framework is set on the FrontendConfig (defensive —
      callers gate on this too).
    * The configured package manager (and the ``npm`` fallback) is not
      on PATH — developer machines without a Node toolchain shouldn't
      see spurious failures; same pattern as ``run_lane_smoke``'s
      docker-availability check.
    """
    from forge.config import FrontendFramework  # noqa: PLC0415

    framework = getattr(fe_cfg, "framework", None)
    if framework is None or framework == FrontendFramework.NONE:
        return _FrontendVerifyOutcome(status="skip", skip_reason="no frontend configured")
    if getattr(framework, "value", None) == "flutter":
        return _FrontendVerifyOutcome(
            status="skip",
            skip_reason="flutter frontend (covered by tests/e2e/test_full_generation.py)",
        )
    pm = getattr(fe_cfg, "package_manager", "npm") or "npm"
    pm_exe = shutil.which(pm) or shutil.which("npm")
    if pm_exe is None:
        return _FrontendVerifyOutcome(
            status="skip", skip_reason=f"{pm} / npm not on PATH (no JS runtime)"
        )
    err = _run_frontend_step(
        pm, pm_exe, fe_dir, ["install", "--no-fund", "--no-audit"], _INSTALL_TIMEOUT_SECONDS
    ) or _run_frontend_step(pm, pm_exe, fe_dir, ["run", "build"], _BUILD_TIMEOUT_SECONDS)
    if err is None:
        return _FrontendVerifyOutcome(status="ok")
    return _FrontendVerifyOutcome(status="fail", error=err)


def _run_frontend_step(
    pm: str, pm_exe: str, fe_dir: Path, args: list[str], timeout: int
) -> str | None:
    """Run a single package-manager step; return failure label or None.

    Translates :class:`subprocess.TimeoutExpired` and
    :class:`FileNotFoundError` into failure labels rather than letting
    them propagate out of the runner — a runaway ``npm install`` must
    not take out the whole matrix run by crashing ``main()``. Mirrors
    the pattern in ``forge.toolchains._runner._run_check``.
    """
    import subprocess  # noqa: PLC0415

    label = f"{pm} {' '.join(args)}"
    try:
        result = subprocess.run(
            [pm_exe, *args],
            cwd=fe_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"{label} timed out after {timeout}s"
    except FileNotFoundError as e:
        return f"{label} could not exec {pm_exe}: {e}"
    if result.returncode == 0:
        return None
    stream = result.stderr.strip() or result.stdout.strip()
    tail = "\n".join(stream.splitlines()[-3:]) if stream else "<no output>"
    return f"{label} failed (exit {result.returncode}): {tail}"


def _inject_weld_stubs(project_root: Path) -> None:
    """Drop matrix-CI weld-* SDK stub packages into ``<project>/packages/``.

    Generated Python services declare ``[tool.uv.sources]`` entries
    pointing at ``../../packages/weld-<name>/`` — the platform monorepo's
    in-tree SDK paths. Matrix CI has no platform sibling tree, so the
    real weld-* sources are unavailable and ``uv sync`` fails. These
    minimal namespace-package stubs (defined under
    ``tests/matrix/fixtures/packages/``) let the verify lane run end-to-end
    (uv sync → ruff → ty → pytest) without the real weld monorepo.

    The stubs expose the same import surface as ``weld-*`` and contain
    just enough behavior for the template's tests to pass against
    ``sqlite+aiosqlite``: TenantMixin/UserOwnedMixin/TimestampMixin
    register SQLAlchemy columns, AsyncBaseRepository implements
    ``_get_base_query`` / ``_to_schema`` / CRUD verbs, the auth
    fragment's ``Error`` envelope round-trips, ``Account`` normalizes
    UUID strings, and so on.

    Idempotent — ``weld-*`` sub-dirs are skipped if already present
    (the auth fragment ships its own ``packages/platform-auth/``).
    """
    if not WELD_STUBS_DIR.is_dir():
        return
    packages_dir = project_root / "packages"
    packages_dir.mkdir(parents=True, exist_ok=True)
    for stub in WELD_STUBS_DIR.iterdir():
        if not stub.is_dir() or not stub.name.startswith("weld-"):
            continue
        target = packages_dir / stub.name
        if target.exists():
            continue
        shutil.copytree(str(stub), str(target))


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    lanes: tuple[Lane, ...]
    port_base: int
    expected_files: tuple[str, ...]
    config: dict[str, Any]
    # Lane D (roundtrip) — when True (the default), lane D MUST find at
    # least one literal FORGE sentinel block to mutate AND produce at
    # least one ``safe-apply`` block candidate after the synthetic edit;
    # otherwise the lane fails. Scenarios that legitimately produce zero
    # block candidates (e.g. a backend whose templates carry no Jinja-
    # free sentinel blocks today) set ``expect_candidates: false`` in
    # ``scenarios.yaml`` to opt out — the lane then reports the empty
    # case as ``ok`` with a documented note. Initiative #9: removes the
    # silent vacuous-green path that previously masked Lane D regressions
    # whenever the fragment surface drifted such that no literal sentinel
    # block remained on disk.
    expect_candidates: bool = True


@dataclass
class LaneResult:
    scenario: str
    lane: Lane
    status: Literal["ok", "fail", "skip"]
    duration_ms: int
    details: str = ""
    missing_files: list[str] = field(default_factory=list)
    # Initiative #9: sub-lane skips (e.g. lane B's frontend check
    # silently returning None when npm isn't on PATH). The lane
    # status stays ``ok`` because the check that COULD run all
    # succeeded, but the skipped sub-checks are surfaced as GitHub
    # Actions warnings via ``_emit_github_annotations`` so a "green"
    # nightly doesn't mask under-coverage from a missing runtime.
    skipped_subchecks: list[str] = field(default_factory=list)


def load_scenarios(path: Path = SCENARIOS_YAML) -> list[Scenario]:
    """Parse ``scenarios.yaml`` into :class:`Scenario` records.

    Raises ``ValueError`` on schema violations — the runner treats that
    as exit-code 3 (misconfiguration) rather than per-scenario failure.
    """
    if not path.exists():
        raise ValueError(f"scenarios.yaml not found at {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("scenarios.yaml must be a mapping at the top level")

    version = data.get("schema_version")
    if version != 1:
        raise ValueError(f"scenarios.yaml schema_version must be 1, got {version!r}")

    raw_scenarios = data.get("scenarios")
    if not isinstance(raw_scenarios, list) or not raw_scenarios:
        raise ValueError("scenarios.yaml must define a non-empty 'scenarios' list")

    scenarios: list[Scenario] = []
    seen_names: set[str] = set()
    seen_ports: dict[int, str] = {}
    for i, raw in enumerate(raw_scenarios):
        if not isinstance(raw, dict):
            raise ValueError(f"scenario #{i} is not a mapping")
        name = raw.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"scenario #{i} missing non-empty 'name'")
        if name in seen_names:
            raise ValueError(f"scenario name {name!r} duplicated")
        seen_names.add(name)
        port_base = raw.get("port_base")
        if not isinstance(port_base, int):
            raise ValueError(f"scenario {name!r} needs integer 'port_base'")
        if port_base in seen_ports:
            raise ValueError(
                f"scenario {name!r} reuses port_base {port_base} already claimed "
                f"by {seen_ports[port_base]!r}"
            )
        seen_ports[port_base] = name

        lanes_raw = raw.get("lanes", list(ALL_LANES))
        if not isinstance(lanes_raw, list):
            raise ValueError(f"scenario {name!r} 'lanes' must be a list")
        for lane in lanes_raw:
            if lane not in ALL_LANES:
                raise ValueError(
                    f"scenario {name!r} has unknown lane {lane!r}; expected one of {ALL_LANES}"
                )

        expected = raw.get("expected_files", [])
        if not isinstance(expected, list) or not all(isinstance(f, str) for f in expected):
            raise ValueError(f"scenario {name!r} 'expected_files' must be list[str]")

        cfg = raw.get("config")
        if not isinstance(cfg, dict):
            raise ValueError(f"scenario {name!r} 'config' must be a mapping")

        expect_candidates = raw.get("expect_candidates", True)
        if not isinstance(expect_candidates, bool):
            raise ValueError(
                f"scenario {name!r} 'expect_candidates' must be bool (got {type(expect_candidates).__name__})"
            )

        scenarios.append(
            Scenario(
                name=name,
                description=raw.get("description", ""),
                lanes=tuple(lanes_raw),
                port_base=port_base,
                expected_files=tuple(expected),
                config=cfg,
                expect_candidates=expect_candidates,
            )
        )
    return scenarios


def _project_config_from_dict(cfg: dict[str, Any]):
    """Build a ``ProjectConfig`` from a scenario config dict.

    Reuses :func:`forge.cli.builder._build_config` via an empty argparse
    namespace so the CLI's merge rules (config-file > default) apply —
    the runner exercises the same ingestion path a ``forge --config foo.yaml``
    invocation does.
    """
    from forge.cli.builder import _build_config  # noqa: PLC0415

    ns = argparse.Namespace()
    # Every flag the builder may introspect; set to None so the config
    # file wins uniformly.
    for attr in (
        "project_name",
        "frontend",
        "yes",
        "quiet",
        "json_output",
        "no_docker",
        "backend_port",
        "python_version",
        "features",
        "description",
        "set_options",
        "backend_language",
        "backend_name",
        "include_auth",
        "include_chat",
        "include_openapi",
        "frontend_port",
        "color_scheme",
        "author_name",
        "package_manager",
        "org_name",
        "api_base_url",
        "api_proxy_target",
        "generate_e2e_tests",
        "include_keycloak",
        "keycloak_port",
        "keycloak_realm",
        "keycloak_url",
        "node_version",
        "rust_edition",
        "output_dir",
    ):
        setattr(ns, attr, None)

    return _build_config(ns, cfg)


def run_lane_generate(scenario: Scenario) -> LaneResult:
    """Lane A: generate into a tmp dir, assert expected files exist."""
    from forge.errors import ForgeError  # noqa: PLC0415
    from forge.generator import generate  # noqa: PLC0415

    start = perf_counter()
    tmp = Path(tempfile.mkdtemp(prefix=f"forge-matrix-{scenario.name}-"))
    try:
        cfg_copy = dict(scenario.config)
        cfg_copy["output_dir"] = str(tmp)
        try:
            project_config = _project_config_from_dict(cfg_copy)
            project_config.validate()
        except (ValueError, ForgeError) as e:
            return LaneResult(
                scenario=scenario.name,
                lane="generate",
                status="fail",
                duration_ms=int((perf_counter() - start) * 1000),
                details=f"config build/validate failed: {e}",
            )

        try:
            project_root = generate(project_config, quiet=True, dry_run=False)
        except ForgeError as e:
            return LaneResult(
                scenario=scenario.name,
                lane="generate",
                status="fail",
                duration_ms=int((perf_counter() - start) * 1000),
                details=f"generate raised {type(e).__name__}({e.code}): {e.message}",
            )

        missing = [f for f in scenario.expected_files if not (project_root / f).exists()]
        if missing:
            return LaneResult(
                scenario=scenario.name,
                lane="generate",
                status="fail",
                duration_ms=int((perf_counter() - start) * 1000),
                details="expected files missing",
                missing_files=missing,
            )
        return LaneResult(
            scenario=scenario.name,
            lane="generate",
            status="ok",
            duration_ms=int((perf_counter() - start) * 1000),
        )
    finally:
        # Windows can hold file handles open briefly after Copier finishes;
        # ignore_errors keeps the runner robust without leaking tmp dirs
        # in CI (they land under %TEMP% and get reaped by the OS).
        shutil.rmtree(tmp, ignore_errors=True)


def run_lane_verify(scenario: Scenario) -> LaneResult:
    """Lane B: generate, then invoke each backend's toolchain.verify().

    Keeps the generated project on disk after lane A succeeds so the
    toolchains can operate on real files, then tears it down. Surfaces
    a failed :class:`Check` as a lane-B failure with the names of the
    failing checks in ``details``.
    """
    from forge.config import BACKEND_REGISTRY  # noqa: PLC0415
    from forge.errors import ForgeError  # noqa: PLC0415
    from forge.generator import generate  # noqa: PLC0415

    start = perf_counter()
    tmp = Path(tempfile.mkdtemp(prefix=f"forge-matrix-{scenario.name}-verify-"))
    try:
        cfg_copy = dict(scenario.config)
        cfg_copy["output_dir"] = str(tmp)
        try:
            project_config = _project_config_from_dict(cfg_copy)
            project_config.validate()
        except (ValueError, ForgeError) as e:
            return LaneResult(
                scenario=scenario.name,
                lane="verify",
                status="fail",
                duration_ms=int((perf_counter() - start) * 1000),
                details=f"config build/validate failed: {e}",
            )
        try:
            project_root = generate(project_config, quiet=True, dry_run=False)
        except ForgeError as e:
            return LaneResult(
                scenario=scenario.name,
                lane="verify",
                status="fail",
                duration_ms=int((perf_counter() - start) * 1000),
                details=f"generate raised {type(e).__name__}({e.code}): {e.message}",
            )

        # Drop minimal weld-* stub SDKs into <project>/packages/ so Python
        # services' [tool.uv.sources] entries resolve. See
        # _inject_weld_stubs docstring for the rationale.
        _inject_weld_stubs(project_root)

        # Initiative #6: a ``skip`` :class:`Check` (e.g. ``uv`` / ``cargo`` /
        # ``npx`` not on PATH) used to be dropped from BOTH ``failures`` and
        # ``skipped_subchecks``, so a lane where EVERY backend check skipped
        # reported ``ok`` while running nothing — masking a CI image
        # regression that stripped the toolchain. Surface skips here, and
        # track whether any real (ok/warn/fail) check ran so an all-skip
        # lane reports ``skip`` rather than a vacuous green ``ok``.
        skipped_subchecks: list[str] = []
        failures: list[str] = []
        ran_backend_check = False
        for bc in project_config.backends:
            spec = BACKEND_REGISTRY[bc.language]
            backend_dir = project_root / "services" / bc.name
            try:
                spec.toolchain.install(backend_dir, quiet=True)
                checks = spec.toolchain.verify(backend_dir, quiet=True)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{bc.name}: toolchain raised {type(exc).__name__}: {exc}")
                continue
            for check in checks:
                if check.status == "fail":
                    failures.append(f"{bc.name}:{check.name}")
                    ran_backend_check = True
                elif check.status == "skip":
                    reason = f": {check.details}" if check.details else ""
                    skipped_subchecks.append(f"{bc.name}:{check.name} skipped{reason}")
                else:
                    # ``ok`` / ``warn`` — a real check actually executed.
                    ran_backend_check = True

        # Frontend build check — catches vue-tsc / svelte-check / vite build
        # regressions on PR rather than waiting for nightly compose-up (lane
        # C). ``frontend_mode`` is the project-level option discriminator
        # (``options["frontend.mode"]``); ``_validate_frontend_mode_coherence``
        # already keeps it in lock-step with ``FrontendConfig.framework``.
        # Every app of the project, each in its own apps/<name>.
        if project_config.frontend_mode != "none":
            for fe_cfg in project_config.active_frontends:
                label = (
                    "frontend"
                    if project_config.is_primary_frontend(fe_cfg)
                    else f"frontend[{fe_cfg.name}]"
                )
                fe_outcome = _verify_frontend(fe_cfg, project_root / "apps" / fe_cfg.name)
                if fe_outcome.status == "fail":
                    failures.append(f"{label}:{fe_outcome.error}")
                elif fe_outcome.status == "skip":
                    # Initiative #9: surface the skip so a "green" lane B
                    # doesn't quietly mean "frontend build was never run".
                    skipped_subchecks.append(f"{label}: {fe_outcome.skip_reason}")

        if failures:
            return LaneResult(
                scenario=scenario.name,
                lane="verify",
                status="fail",
                duration_ms=int((perf_counter() - start) * 1000),
                details="toolchain checks failed: " + "; ".join(failures),
                skipped_subchecks=skipped_subchecks,
            )
        # Initiative #6: verify is a required lane. If the scenario had
        # backends but NONE of their checks actually ran (every one
        # skipped because the toolchain CLI was missing), the lane
        # verified nothing — report ``skip`` so it can't masquerade as a
        # green that exercised the toolchain.
        if project_config.backends and not ran_backend_check:
            return LaneResult(
                scenario=scenario.name,
                lane="verify",
                status="skip",
                duration_ms=int((perf_counter() - start) * 1000),
                details="all backend toolchain checks skipped (no toolchain CLI on PATH)",
                skipped_subchecks=skipped_subchecks,
            )
        return LaneResult(
            scenario=scenario.name,
            lane="verify",
            status="ok",
            duration_ms=int((perf_counter() - start) * 1000),
            skipped_subchecks=skipped_subchecks,
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_lane_smoke(scenario: Scenario) -> LaneResult:
    """Lane C: generate, compose-up, assert contract, compose-down.

    Requires ``docker`` on PATH. Skipped (not failed) when docker is
    unavailable so developer machines without Docker don't show spurious
    failures; CI is expected to have Docker and will exercise this.
    """
    import subprocess  # noqa: PLC0415

    # ``tests.matrix.smoke_contract`` is a sibling module; when this file
    # is invoked as a script (``uv run python tests/matrix/runner.py``)
    # the script's own directory — not the repo root — is on sys.path,
    # so the absolute ``from tests.matrix...`` import below would
    # ``ModuleNotFoundError`` on the matrix-nightly CI runner. Add the
    # repo root once, idempotently, before the absolute import.
    _repo_root = str(Path(__file__).resolve().parents[2])
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)

    from forge.errors import ForgeError  # noqa: PLC0415
    from forge.generator import generate  # noqa: PLC0415
    from tests.matrix.smoke_contract import assert_contract  # noqa: PLC0415

    docker_exe = shutil.which("docker")
    if docker_exe is None:
        return LaneResult(
            scenario=scenario.name,
            lane="smoke",
            status="skip",
            duration_ms=0,
            details="docker not on PATH",
        )

    start = perf_counter()
    tmp = Path(tempfile.mkdtemp(prefix=f"forge-matrix-{scenario.name}-smoke-"))
    project_root: Path | None = None
    compose_attempted = False
    try:
        cfg_copy = dict(scenario.config)
        cfg_copy["output_dir"] = str(tmp)
        try:
            project_config = _project_config_from_dict(cfg_copy)
            project_config.validate()
            project_root = generate(project_config, quiet=True, dry_run=False)
        except (ValueError, ForgeError) as e:
            return LaneResult(
                scenario=scenario.name,
                lane="smoke",
                status="fail",
                duration_ms=int((perf_counter() - start) * 1000),
                details=f"generate failed: {e}",
            )

        # Drop weld-* stubs into <project>/packages/ so Python-service Docker
        # builds (which run ``uv sync`` against the same [tool.uv.sources])
        # have something to resolve against. Same hook as the verify lane.
        _inject_weld_stubs(project_root)

        compose_file = project_root / "docker-compose.yml"
        if not compose_file.exists():
            return LaneResult(
                scenario=scenario.name,
                lane="smoke",
                status="skip",
                duration_ms=int((perf_counter() - start) * 1000),
                details="no docker-compose.yml rendered (scenario likely headless)",
            )

        # Bring up the whole stack detached. --wait blocks on each service's
        # healthcheck (or a default readiness guess) — much more reliable
        # than polling ourselves. Compose-v2 only; Compose-v1 will fail
        # and we surface it as a lane-C fail rather than try to simulate.
        compose_attempted = True
        up_result = subprocess.run(
            # ``--build`` is essential: without it compose reuses any cached
            # image of the same name from a prior run, so the smoke lane would
            # silently test STALE generated source (a fixed template could
            # appear to still ship the bug). Mirrors the real deploy path in
            # ``forge.docker_manager`` which also builds.
            [docker_exe, "compose", "-f", str(compose_file), "up", "-d", "--wait", "--build"],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if up_result.returncode != 0:
            # Persist the FULL build/up output to the artifact dir. When the
            # IMAGE BUILD fails (tsc exit 2 / cargo exit 101) no container
            # starts, so the finally-block's ``docker compose logs`` is empty —
            # the only record of the compiler error is here, in up_result. The
            # old 5-line console tail truncated it, which is exactly what made
            # the PR #170 node/rust image-build break un-diagnosable from CI.
            log_dir = os.environ.get("FORGE_MATRIX_LOG_DIR")
            if log_dir:
                out_dir = Path(log_dir)
                out_dir.mkdir(parents=True, exist_ok=True)
                (out_dir / f"{scenario.name}-compose-up.log").write_text(
                    f"$ docker compose up -d --wait --build (exit {up_result.returncode})\n"
                    f"--- stdout ---\n{up_result.stdout}\n"
                    f"--- stderr ---\n{up_result.stderr}\n",
                    encoding="utf-8",
                )
            stderr_tail = "\n".join(up_result.stderr.strip().splitlines()[-20:])
            return LaneResult(
                scenario=scenario.name,
                lane="smoke",
                status="fail",
                duration_ms=int((perf_counter() - start) * 1000),
                details=(
                    f"docker compose up failed (exit {up_result.returncode}); "
                    f"full output in {scenario.name}-compose-up.log:\n{stderr_tail}"
                ),
            )
        # Run the HTTP contract against each backend.
        violations: list[str] = []
        sub_skips: list[str] = []
        for bc in project_config.backends:
            base_url = f"http://localhost:{bc.server_port}"
            # Pass the backend's CRUD entities so the contract exercises the
            # data path (GET list + POST create) and a 5xx there — e.g. a
            # no-auth backend that never binds a request identity — is caught
            # instead of slipping through on a green health + OpenAPI check.
            result = assert_contract(
                base_url, scenario.name, bc.name, crud_entities=list(bc.features)
            )
            if not result.passed:
                violations.extend(f"{bc.name}:{v.endpoint}: {v.reason}" for v in result.violations)
            # Initiative #9 — propagate per-backend endpoint skips to the
            # lane result so the annotation emitter can surface them.
            sub_skips.extend(f"{bc.name}: {s}" for s in result.skipped_endpoints)

        if violations:
            return LaneResult(
                scenario=scenario.name,
                lane="smoke",
                status="fail",
                duration_ms=int((perf_counter() - start) * 1000),
                details="contract violations: "
                + "; ".join(violations[:5])
                + (f" (+{len(violations) - 5} more)" if len(violations) > 5 else ""),
                skipped_subchecks=sub_skips,
            )
        return LaneResult(
            scenario=scenario.name,
            lane="smoke",
            status="ok",
            duration_ms=int((perf_counter() - start) * 1000),
            skipped_subchecks=sub_skips,
        )
    finally:
        if project_root is not None:
            compose_file = project_root / "docker-compose.yml"
            if compose_file.exists():
                # Capture compose logs + ps into FORGE_MATRIX_LOG_DIR (if
                # set) BEFORE tearing the stack down. CI jobs read these
                # as artifact input — see .github/workflows/{ci,matrix-
                # nightly}.yml. Doing it inside the runner avoids the
                # race where a post-job step tries to read /tmp dirs we've
                # already removed.
                #
                # NB: dumping is gated only on ``compose_file.exists()``,
                # NOT on success — when ``docker compose up`` itself
                # fails mid-way, the containers that DID start (or
                # partially started) hold exactly the diagnostics we need.
                # Skipping the dump on the failure path is what made
                # ``api container exits code 3 with no captured logs``
                # un-debuggable in matrix-CI prior to this fix.
                log_dir = os.environ.get("FORGE_MATRIX_LOG_DIR")
                if log_dir:
                    _dump_compose_diagnostics(
                        docker_exe, compose_file, Path(log_dir), scenario.name
                    )
                # A failed/timeout bring-up can leave started containers and
                # named volumes behind. Tear down every attempted stack.
                if compose_attempted:
                    subprocess.run(
                        [
                            docker_exe,
                            "compose",
                            "-f",
                            str(compose_file),
                            "down",
                            "-v",
                            "--remove-orphans",
                        ],
                        capture_output=True,
                        timeout=120,
                        check=False,
                    )
        shutil.rmtree(tmp, ignore_errors=True)


def _dump_compose_diagnostics(
    docker_exe: str, compose_file: Path, log_dir: Path, scenario_name: str
) -> None:
    """Write ``docker compose logs`` + ``ps`` into ``log_dir`` for triage.

    Best-effort: a failure to capture must not mask the upstream lane
    failure, so all subprocesses run with ``check=False`` and short
    timeouts and any OSError on the file write is swallowed.
    """
    import subprocess  # noqa: PLC0415

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    log_path = log_dir / f"{scenario_name}.log"
    ps_path = log_dir / f"{scenario_name}.ps"
    with (
        contextlib.suppress(OSError, subprocess.TimeoutExpired),
        log_path.open("w", encoding="utf-8") as fh,
    ):
        subprocess.run(
            [docker_exe, "compose", "-f", str(compose_file), "logs", "--no-color"],
            stdout=fh,
            stderr=subprocess.STDOUT,
            timeout=60,
            check=False,
        )
    with (
        contextlib.suppress(OSError, subprocess.TimeoutExpired),
        ps_path.open("w", encoding="utf-8") as fh,
    ):
        subprocess.run(
            [docker_exe, "compose", "-f", str(compose_file), "ps"],
            stdout=fh,
            stderr=subprocess.STDOUT,
            timeout=30,
            check=False,
        )


def run_lane_roundtrip(scenario: Scenario) -> LaneResult:
    """Lane D: bidirectional round-trip CI gate (Phase 5/6).

    v2 contract — what lane D enforces today:
      1. **Generate** the scenario into ``project-a``.
      2. **FR1** — :func:`harvest_project` on the fresh project MUST
         emit zero ``"block"`` and zero ``"files"`` candidates. Lane
         D fails on a positive count; the rationale + offending
         candidates surface in ``details``.
      3. **Apply-back round-trip** — when the project ships at least
         one literal-text FORGE sentinel block, stage a synthetic edit,
         harvest, apply the bundle to the LIVE forge tree (with
         snapshot+restore in ``finally`` so other parallel lanes don't
         see polluted fragments), regenerate into ``project-b``, and
         assert that the two project trees match modulo documented
         noise (emitted_at timestamps, sentinel fingerprints, manifest
         sha256 fields, .git/, .copier-answers.yml).
      4. **Vacuously-true scenarios** — projects without any literal
         sentinel block (no Jinja-free apply-back-capable site) land
         as ``ok`` with a note. The round-trip contract is empty for
         block-less scenarios.

    Lane D opts in per-scenario via ``scenarios.yaml``. Nightly CI
    runs the opted-in set; PR CI doesn't touch this lane unless a
    contributor explicitly invokes it (``--scenario X --lane roundtrip``).
    """
    from forge.errors import ForgeError  # noqa: PLC0415
    from forge.generator import generate  # noqa: PLC0415
    from forge.sync.project_to_forge import (  # noqa: PLC0415
        apply_bundle_to_fragments,
        harvest_project,
        verify_project,
    )

    start = perf_counter()
    tmp = Path(tempfile.mkdtemp(prefix=f"forge-matrix-{scenario.name}-roundtrip-"))
    try:
        cfg_copy = dict(scenario.config)
        cfg_copy["output_dir"] = str(tmp / "project-a")
        try:
            project_config = _project_config_from_dict(cfg_copy)
            project_config.validate()
            project_a = generate(project_config, quiet=True, dry_run=False)
        except (ValueError, ForgeError) as e:
            return LaneResult(
                scenario=scenario.name,
                lane="roundtrip",
                status="fail",
                duration_ms=int((perf_counter() - start) * 1000),
                details=f"first generate failed: {e}",
            )

        # FR1 — fresh-generate harvest must produce zero block/files
        # candidates. ``deps`` + ``env`` legitimately surface base-
        # template names that no fragment claims; scope the assertion
        # to the kinds that ``apply_bundle_to_fragments`` covers.
        bundle_pre = harvest_project(project_a, quiet=True)
        fr1_offenders = [c for c in bundle_pre.candidates if c.kind in ("block", "files")]
        if fr1_offenders:
            return LaneResult(
                scenario=scenario.name,
                lane="roundtrip",
                status="fail",
                duration_ms=int((perf_counter() - start) * 1000),
                details=(
                    f"FR1 violation: fresh-generate produced "
                    f"{len(fr1_offenders)} block/files candidate(s); "
                    f"first: {fr1_offenders[0].fragment}/{fr1_offenders[0].rel_path}"
                ),
            )

        # Day-0 verify must be clean too: a virgin project that fails
        # ``forge --verify`` (exit 10) is the P3.6.1 regression — the deps/env
        # appliers mutate manifests after provenance stamps them, and
        # generate() now re-records them. The 3-language unit test
        # (tests/test_verify_fresh_generate.py) locks this for one config each;
        # asserting it here locks it across the whole scenario grid so a future
        # applier that forgets to re-record can't ship green. verify_project is
        # read-only — no restore step, safe alongside the apply-back below.
        vres = verify_project(Path(project_a), scope="all", fail_on="drift")
        if vres.worst != "clean":
            drifted = [r.rel_path for r in vres.records if r.status != "unchanged"]
            return LaneResult(
                scenario=scenario.name,
                lane="roundtrip",
                status="fail",
                duration_ms=int((perf_counter() - start) * 1000),
                details=(
                    f"day-0 `forge --verify` not clean ({vres.worst}); drift on {drifted[:8]}"
                ),
            )

        # Edit a non-Jinja sentinel block (apply-back literalizes the
        # user's body; Jinja-bearing blocks round-trip incorrectly and
        # are flagged needs-review at harvest anyway).
        #
        # Initiative #9: the two "no candidates" exit paths below
        # (no literal block found, or no safe-apply after edit) used to
        # report ``ok`` unconditionally — a silent vacuous-green that
        # masked Lane D regressions whenever the fragment surface drifted
        # such that no literal sentinel block remained on disk. The lane
        # now requires scenarios to opt INTO empty-candidate vacuous-true
        # by setting ``expect_candidates: false`` in scenarios.yaml.
        # Default (``expect_candidates: true``) treats the empty case as
        # a regression.
        edited_path = _edit_one_literal_block(project_a)
        if edited_path is None:
            return _empty_candidate_result(
                scenario,
                start,
                reason=(
                    "no literal FORGE sentinel block to exercise the apply-back "
                    "round-trip (vacuously round-trippable)"
                ),
                gate_message=(
                    "FR1 passed but no literal FORGE sentinel block was found "
                    f"under {scenario.name!r}; lane D had nothing to mutate so "
                    "the apply-back round-trip was vacuously satisfied. If this "
                    "is intentional (e.g. every fragment in this scenario is "
                    "Jinja-templated), set `expect_candidates: false` on the "
                    "scenario in tests/matrix/scenarios.yaml. Otherwise restore "
                    "the literal block surface that lane D relies on."
                ),
            )

        # Harvest the user-edit. Filter to block candidates only —
        # deps/env are legitimately deferred (not part of FR2's
        # contract).
        bundle_post = harvest_project(project_a, quiet=True)
        bundle_post.candidates[:] = [c for c in bundle_post.candidates if c.kind == "block"]
        if not any(c.risk == "safe-apply" for c in bundle_post.candidates):
            return _empty_candidate_result(
                scenario,
                start,
                reason=(
                    "no safe-apply block candidate after edit (fragment must be Jinja-templated)"
                ),
                gate_message=(
                    "FR1 passed and a literal block was edited, but harvest "
                    f"produced zero safe-apply block candidates for {scenario.name!r}. "
                    "The bundle's apply-back contract is empty, so the FR2 "
                    "round-trip cannot run. If every fragment in this scenario "
                    "is Jinja-templated (round-trip not applicable), set "
                    "`expect_candidates: false` on the scenario in "
                    "tests/matrix/scenarios.yaml. Otherwise investigate why the "
                    "edit didn't surface a safe-apply candidate."
                ),
            )

        # Initiative #9: apply the bundle to a tempdir SANDBOX of the
        # forge tree (cp -a or git worktree) and run the second generate
        # via subprocess pointed at that sandbox. The previous design
        # mutated the LIVE forge tree under
        # ``snapshot/finally restore``; under parallel CI (multiple
        # scenarios on the same runner, or a developer running another
        # ``forge`` command concurrently) the snapshot/restore raced and
        # could leave inject.yaml files in a half-restored state if the
        # process was killed between mutate and restore. The sandbox is
        # discarded with ``shutil.rmtree(tmp)`` so no restore step is
        # needed and concurrent invocations don't share state.
        sandbox_forge_root = _materialize_forge_sandbox(tmp / "forge-sandbox")
        try:
            report = apply_bundle_to_fragments(bundle_post, sandbox_forge_root, quiet=True)
        except Exception as exc:  # noqa: BLE001
            return LaneResult(
                scenario=scenario.name,
                lane="roundtrip",
                status="fail",
                duration_ms=int((perf_counter() - start) * 1000),
                details=f"apply-back raised {type(exc).__name__}: {exc}",
            )
        if report.errored:
            first_err = next((e.error for e in report.entries if e.status == "errored"), "")
            return LaneResult(
                scenario=scenario.name,
                lane="roundtrip",
                status="fail",
                duration_ms=int((perf_counter() - start) * 1000),
                details=(
                    f"apply-back errored on {report.errored} block candidate(s); first: {first_err}"
                ),
            )

        cfg_b = dict(scenario.config)
        cfg_b["output_dir"] = str(tmp / "project-b")
        project_b_or_fail = _subprocess_generate(
            cfg_b,
            sandbox_forge_root=sandbox_forge_root,
            project_name=scenario.config.get("project_name") or scenario.name,
        )
        if isinstance(project_b_or_fail, str):
            return LaneResult(
                scenario=scenario.name,
                lane="roundtrip",
                status="fail",
                duration_ms=int((perf_counter() - start) * 1000),
                details=f"second generate failed: {project_b_or_fail}",
            )
        project_b = project_b_or_fail

        differing = _diff_project_trees_normalized(project_a, project_b)
        if differing:
            return LaneResult(
                scenario=scenario.name,
                lane="roundtrip",
                status="fail",
                duration_ms=int((perf_counter() - start) * 1000),
                details=(
                    f"project_a vs project_b: {len(differing)} file(s) "
                    f"differ after normalisation; first: {differing[0]}"
                ),
            )

        return LaneResult(
            scenario=scenario.name,
            lane="roundtrip",
            status="ok",
            duration_ms=int((perf_counter() - start) * 1000),
            details=(
                f"FR1 passed; FR2 round-trip passed (applied={report.applied} block candidate(s))"
            ),
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _empty_candidate_result(
    scenario: Scenario,
    start: float,
    *,
    reason: str,
    gate_message: str,
) -> LaneResult:
    """Return ``ok`` or ``fail`` per ``scenario.expect_candidates`` gate.

    Centralises the Initiative #9 anti-vacuous-green policy. When a
    scenario opted out (``expect_candidates: false``) the empty-candidate
    case is documented as ``ok`` with ``reason`` in details. When the
    scenario did NOT opt out (the default), the empty case fails loudly
    with ``gate_message`` so a future template drift doesn't silently
    coast through lane D as green.
    """
    duration_ms = int((perf_counter() - start) * 1000)
    if scenario.expect_candidates:
        return LaneResult(
            scenario=scenario.name,
            lane="roundtrip",
            status="fail",
            duration_ms=duration_ms,
            details=gate_message,
        )
    return LaneResult(
        scenario=scenario.name,
        lane="roundtrip",
        status="ok",
        duration_ms=duration_ms,
        details=f"FR1 passed; {reason} (expect_candidates=false)",
    )


def _edit_one_literal_block(project_root: Path) -> Path | None:
    """Inject a synthetic edit inside the first NON-JINJA FORGE block found.

    Walks ``project_root`` for a text file containing a ``FORGE:BEGIN``
    / ``FORGE:END`` pair whose body has no ``{{ }}`` / ``{% %}`` tokens
    (Jinja-bearing blocks round-trip incorrectly through apply-back —
    they'd re-render to a different body on the second generate, and
    the harvest already filters them to ``needs-review`` so they
    wouldn't apply under the default risk filter anyway). Inserts a
    comment line at the START of the block body and returns the edited
    path. ``None`` when no eligible block is found.
    """
    for ext in (".py", ".ts", ".js", ".rs", ".go"):
        for path in sorted(project_root.rglob(f"*{ext}")):
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            idx_begin = text.find("FORGE:BEGIN ")
            idx_end = text.find("FORGE:END ")
            if idx_begin == -1 or idx_end == -1 or idx_end <= idx_begin:
                continue

            begin_line_end = text.find("\n", idx_begin)
            if begin_line_end == -1:
                continue
            body_start = begin_line_end + 1
            end_line_start = text.rfind("\n", 0, idx_end) + 1
            body = text[body_start:end_line_start]

            # Skip blocks with Jinja syntax — apply-back literalizes
            # the body and round-trip would diverge on the second
            # generate's re-render.
            if "{{" in body or "{%" in body:
                continue

            # Pick the comment prefix off the BEGIN sentinel line so
            # the injected marker matches the surrounding syntax.
            before_begin = text.rfind("\n", 0, idx_begin) + 1
            begin_line = text[before_begin:idx_begin]
            comment_prefix = begin_line.rstrip().rstrip("FORGE:").rstrip()
            if not comment_prefix:
                comment_prefix = "# "
            if not comment_prefix.endswith(" "):
                comment_prefix = comment_prefix + " "

            injection = f"{comment_prefix}forge round-trip CI marker\n"
            new_text = text[:body_start] + injection + text[body_start:]
            path.write_text(new_text, encoding="utf-8")
            return path
    return None


def _live_forge_root() -> Path:
    """Return the directory containing the live ``forge/`` package.

    The runner is invoked from the repo root, so the parent of this
    file's grand-parent is the directory containing ``forge/``. Used
    by :func:`_materialize_forge_sandbox` as the source for lane D's
    sandbox copy.
    """
    return Path(__file__).resolve().parents[2]


_SANDBOX_IGNORE = shutil.ignore_patterns(
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    "*.pyc",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "target",
    "build",
    "dist",
)


def _materialize_forge_sandbox(dest: Path) -> Path:
    """Create a sandbox copy of the live forge package tree at ``dest``.

    Initiative #9: lane D used to mutate ``inject.yaml`` files in the
    LIVE forge tree (with a snapshot+restore-in-``finally`` guard).
    Under parallel CI (multiple scenarios on one runner, or a developer
    running a ``forge`` command concurrently) the snapshot/restore
    raced; a killed process between mutate and restore left the tree
    in a half-restored state. The sandbox sidesteps the race: the
    apply-back writes into a tempdir copy and the tempdir is removed
    with the rest of the lane's scratch space at the end of
    :func:`run_lane_roundtrip`. No restore step exists, so no race.

    Returns ``dest`` (the directory now containing a ``forge/`` subdir
    suitable for use as the ``forge_repo`` argument to
    :func:`apply_bundle_to_fragments`).

    Copies only the ``forge/`` package directory (not the surrounding
    repo) — apply-back only touches
    ``forge/features/.../inject.yaml`` and
    ``forge/templates/_fragments/.../inject.yaml`` paths, and the
    subprocess generate spawned below only needs ``forge/`` itself on
    ``PYTHONPATH``. ``shutil.copytree`` with ``_SANDBOX_IGNORE`` skips
    build artefacts and caches that would otherwise inflate the copy
    by orders of magnitude on a developer machine (the ``forge/``
    package proper is ~30 MB; a venv-laden checkout is closer to 500 MB).
    """
    live = _live_forge_root()
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        str(live / "forge"),
        str(dest / "forge"),
        symlinks=True,
        ignore=_SANDBOX_IGNORE,
        dirs_exist_ok=True,
    )
    return dest


def _subprocess_generate(
    scenario_config: dict[str, Any],
    *,
    sandbox_forge_root: Path,
    project_name: str,
) -> Path | str:
    """Spawn ``python -m forge --config <yaml>`` against the sandbox tree.

    Lane D's second-generate step. Must run in a subprocess (not via a
    direct :func:`forge.generator.generate` call) because the current
    process imported ``forge`` from the live tree at module load —
    ``FRAGMENT_REGISTRY`` entries and ``_TEMPLATES_DIR`` were resolved
    against the live tree's paths and cannot be re-rooted at runtime.
    The subprocess imports ``forge`` from the sandbox (``PYTHONPATH``
    prepend), so the apply-back's inject.yaml edits in the sandbox
    take effect.

    Returns the absolute path to the generated project root on success,
    or a single-line error string on failure (caller surfaces as a
    lane FAIL ``details``). The error includes the subprocess's
    captured stderr tail so a real generator regression doesn't get
    swallowed by an opaque "second generate failed" message.
    """
    import subprocess  # noqa: PLC0415

    output_dir = Path(scenario_config["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    config_yaml = output_dir.parent / "scenario-config.yaml"
    config_yaml.write_text(yaml.safe_dump(scenario_config), encoding="utf-8")

    env = dict(os.environ)
    pythonpath_parts = [str(sandbox_forge_root)]
    if env.get("PYTHONPATH"):
        pythonpath_parts.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)

    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "forge",
                "--config",
                str(config_yaml),
                # ``--output-dir`` must be passed via CLI flag, not the
                # config file's ``output_dir`` key: the CLI's argparse
                # default of ``"."`` wins over the config-file value in
                # the builder's flag>cfg>default resolution order, so
                # leaving it implicit would land the second project at
                # ``cwd/<slug>`` rather than the tempdir we want.
                "--output-dir",
                str(output_dir),
                "--quiet",
                "--yes",
                # ``--yes`` would otherwise trigger ``docker compose up``
                # at the end of generate; lane D only cares about the
                # generated tree contents, not the live stack. Skipping
                # the boot also avoids a flaky tail (compose pulling
                # the network at random) that drowns the diff signal.
                "--no-docker",
            ],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(sandbox_forge_root),
            timeout=600,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "subprocess generate timed out after 600s"
    except FileNotFoundError as e:
        return f"subprocess generate exec failed: {e}"
    if result.returncode != 0:
        stream = result.stderr.strip() or result.stdout.strip()
        tail = "\n".join(stream.splitlines()[-5:]) if stream else "<no output>"
        return f"subprocess generate exit {result.returncode}: {tail}"

    # ``forge`` slugifies the project_name (lowercase, replace spaces +
    # hyphens with underscores) before constructing the project dir;
    # matches ``ProjectConfig.project_slug``. Computed locally so we
    # don't import a ``forge.config`` symbol from the host process —
    # the helper has no business depending on whichever forge module
    # the runner imported at startup, given the whole point of the
    # subprocess is to isolate the second generate from the host's
    # registry state.
    slug = project_name.lower().replace(" ", "_").replace("-", "_")
    project_root = output_dir / slug
    if not project_root.is_dir():
        # Fallback: scan output_dir for the single created subdir.
        # Robust against future slug-rule drift without forcing the
        # helper to mirror forge's slugifier byte-for-byte.
        candidates = [p for p in output_dir.iterdir() if p.is_dir()]
        if len(candidates) == 1:
            return candidates[0]
        return (
            f"subprocess generate succeeded but project dir not found under "
            f"{output_dir} (candidates: {[c.name for c in candidates]!r})"
        )
    return project_root


def _diff_project_trees_normalized(a: Path, b: Path) -> list[str]:
    """Compare two project trees with the FR2-normalisation pipeline.

    Returns a sorted list of POSIX rel-paths that differ. An empty
    list means the trees match modulo:

    * ``.git/...`` — different sha objects across two ``git init`` runs.
    * ``.copier-answers.yml`` — Copier internals (``_commit`` /
      ``_src_path`` may drift).
    * ``emitted_at = "..."`` — UTC-second granularity timestamps in
      ``forge.toml`` provenance entries.
    * ``sha256 = "..."`` / ``snippet_sha256 = "..."`` — derived from
      file/snippet content; project_a's manifest was recorded before
      the user's edit (so it's stale relative to the on-disk file),
      while project_b's is fresh. The contract is about CONTENT
      equality, not manifest metadata.
    * ``fp:<hex8>`` in FORGE BEGIN sentinels — fingerprint of the
      rendered snippet; project_a has the OLD fingerprint (user only
      edited the body), project_b has the NEW one (regenerate
      re-stamps).
    * Generator requirement and source fingerprint in ``.forge/quality.json``
      — project_b uses the edited sandbox, not the original git checkout.
      Recipe configuration, versions, and plugin requirements still compare.

    LF/CRLF normalization is applied to every text file so the
    comparison is platform-tolerant.
    """
    import re  # noqa: PLC0415

    # ``tests._artefact_filters`` is a sibling test-package module. When
    # this file is invoked as a script (``uv run python tests/matrix/runner.py``)
    # the repo root isn't initially on sys.path, mirroring the pattern at
    # ``run_lane_smoke`` above. Add it once before the absolute import.
    _repo_root = str(Path(__file__).resolve().parents[2])
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)
    from tests._artefact_filters import is_generated_artefact  # noqa: PLC0415, E402

    def is_excluded(rel: str) -> bool:
        # Roundtrip-specific exclusions live in tests/_artefact_filters.py —
        # shared with the golden snapshot tests so the two contracts can't
        # drift. Note: FR2 is deliberately narrower than golden snapshots
        # (package-lock.json / auto-imports.d.ts / /api/generated/ stay
        # visible to roundtrip so we catch real lockfile-drift bugs).
        # .copier-answers.yml is FR2-only (golden snapshots record it).
        if rel.endswith(".copier-answers.yml"):
            return True
        return is_generated_artefact(rel)

    def normalize(rel: str, text: str) -> str:
        out = text
        if rel == ".forge/quality.json":
            try:
                recipe = json.loads(out)
            except json.JSONDecodeError:
                return out
            if isinstance(recipe, dict):
                # Retain absent/invalid identity fields so they still differ.
                fingerprint = recipe.get("generator_sha256")
                if isinstance(fingerprint, str) and re.fullmatch(r"[0-9a-f]{64}", fingerprint):
                    recipe["generator_sha256"] = "<NORM>"
                requirement = recipe.get("generator_requirement")
                version = recipe.get("generator_version")
                release_pin = (
                    isinstance(version, str)
                    and bool(version)
                    and requirement == f"forge-cli=={version}"
                )
                # These are the two forms produced by this checkout/sandbox
                # harness. Preserve other remotes, invalid refs and versions.
                checkout_pin = isinstance(requirement, str) and re.fullmatch(
                    r"forge-cli @ git\+https://github\.com/cchifor/forge@[0-9a-f]{40}(?:[0-9a-f]{24})?",
                    requirement,
                )
                if release_pin or checkout_pin:
                    recipe["generator_requirement"] = "<NORM>"
                out = json.dumps(recipe, sort_keys=True)
        if rel.endswith("forge.toml"):
            out = re.sub(r'emitted_at\s*=\s*"[^"]*"', 'emitted_at = "<NORM>"', out)
            out = re.sub(r'sha256\s*=\s*"[0-9a-f]+"', 'sha256 = "<NORM>"', out)
            out = re.sub(
                r'snippet_sha256\s*=\s*"[0-9a-f]+"',
                'snippet_sha256 = "<NORM>"',
                out,
            )
        out = re.sub(r"FORGE:BEGIN ([^\n]*?) fp:[0-9a-f]{8}", r"FORGE:BEGIN \1 fp:<NORM>", out)
        return out

    def is_text_bytes(data: bytes) -> bool:
        if b"\x00" in data[:8192]:
            return False
        try:
            data[:8192].decode("utf-8")
        except UnicodeDecodeError:
            return False
        return True

    files_a = {
        p.relative_to(a).as_posix(): p
        for p in a.rglob("*")
        if p.is_file() and not is_excluded(p.relative_to(a).as_posix())
    }
    files_b = {
        p.relative_to(b).as_posix(): p
        for p in b.rglob("*")
        if p.is_file() and not is_excluded(p.relative_to(b).as_posix())
    }

    differing: list[str] = []
    for rel in sorted(set(files_a) | set(files_b)):
        pa = files_a.get(rel)
        pb = files_b.get(rel)
        if pa is None or pb is None:
            differing.append(rel)
            continue
        ba = pa.read_bytes()
        bb = pb.read_bytes()
        if ba == bb:
            continue
        if not (is_text_bytes(ba) and is_text_bytes(bb)):
            differing.append(rel)
            continue
        sa = normalize(rel, ba.decode("utf-8", errors="replace"))
        sb = normalize(rel, bb.decode("utf-8", errors="replace"))
        if sa == sb or sa.replace("\r\n", "\n") == sb.replace("\r\n", "\n"):
            continue
        differing.append(rel)
    return differing


def run_lane_update(scenario: Scenario) -> LaneResult:
    """Lane E: generate, edit, run forge --update for each mode, verify outcome.

    Drives the three merge modes (``merge`` / ``skip`` / ``overwrite``)
    against an edited file in a fragment-managed zone, then runs
    ``forge --harvest --harvest-out=-`` once and asserts a parseable
    JSON bundle is emitted on stdout. Delegates the per-mode contract
    to :mod:`tests.matrix.update_contract`.

    Unlike lane D (which uses the Python API directly), lane E shells
    out to ``python -m forge`` so the argparse, builder and dispatcher
    path real users hit is exercised end-to-end on every nightly run.
    """
    # When the runner is invoked as a script (``uv run python
    # tests/matrix/runner.py``), only the script directory lands on
    # sys.path — the absolute ``from tests.matrix.update_contract``
    # import below would ``ModuleNotFoundError``. Add the repo root
    # once, idempotently, before the absolute import. Same pattern as
    # ``run_lane_smoke``.
    _repo_root = str(Path(__file__).resolve().parents[2])
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)

    from tests.matrix.update_contract import run_update_contract  # noqa: PLC0415

    return run_update_contract(scenario, _project_config_from_dict, _inject_weld_stubs)


LANE_DISPATCH = {
    "generate": run_lane_generate,
    "verify": run_lane_verify,
    "smoke": run_lane_smoke,
    "roundtrip": run_lane_roundtrip,
    "update": run_lane_update,
}


def run_scenario(scenario: Scenario, lane: Lane) -> LaneResult:
    if lane not in scenario.lanes:
        return LaneResult(
            scenario=scenario.name,
            lane=lane,
            status="skip",
            duration_ms=0,
            details=f"scenario does not opt into lane {lane}",
        )
    return LANE_DISPATCH[lane](scenario)


def _format_row(r: LaneResult) -> str:
    badge = {"ok": "OK  ", "fail": "FAIL", "skip": "SKIP"}.get(r.status, "????")
    base = f"[{badge}] {r.scenario:24s} {r.lane:8s} {r.duration_ms:>6d}ms"
    if r.status == "fail":
        return f"{base}  {r.details}" + (
            f" (missing: {', '.join(r.missing_files)})" if r.missing_files else ""
        )
    if r.status == "skip" and r.details:
        return f"{base}  ({r.details})"
    return base


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="forge config-matrix validation runner")
    parser.add_argument("--all", action="store_true", help="run every scenario")
    parser.add_argument("--scenario", help="run a single scenario by name")
    parser.add_argument(
        "--lane",
        choices=list(ALL_LANES),
        default="generate",
        help="which lane to run (default: generate)",
    )
    parser.add_argument("--list", action="store_true", help="list scenarios and exit")
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit JSON results to stdout (for CI matrix aggregation)",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        help="write this execution's results to a JSON artifact, keeping logs on stdout",
    )
    parser.add_argument(
        "--report",
        choices=["text", "markdown"],
        default="text",
        help=(
            "output format. 'text' is the human-readable table (default); "
            "'markdown' emits a 3-column scenario x lane grid suitable for "
            "committing to docs/matrix-status.md"
        ),
    )
    return parser.parse_args()


def _format_markdown_grid(results: list[LaneResult], scenarios: list[Scenario]) -> str:
    """Render a scenarios × lanes grid as a GitHub-flavored Markdown table.

    Exactly one cell per (scenario, lane). Statuses use simple glyphs so
    the rendered output works in the monospace GitHub UI without
    depending on emoji availability.
    """
    glyph = {"ok": "[OK  ]", "fail": "[FAIL]", "skip": "[SKIP]"}
    # Group results by scenario for O(1) lookup during render.
    by_scenario: dict[str, dict[Lane, LaneResult]] = {}
    for r in results:
        by_scenario.setdefault(r.scenario, {})[r.lane] = r  # type: ignore[index]

    lanes_in_order: tuple[Lane, ...] = ALL_LANES
    lines = [
        "| Scenario | " + " | ".join(lane for lane in lanes_in_order) + " | Notes |",
        "| " + " | ".join(["---"] * (2 + len(lanes_in_order))) + " |",
    ]
    for sc in scenarios:
        row_cells = [sc.name]
        notes_bits: list[str] = []
        for lane in lanes_in_order:
            r = by_scenario.get(sc.name, {}).get(lane)
            if r is None:
                row_cells.append("[—]")
                continue
            row_cells.append(glyph.get(r.status, "[?]"))
            if r.status == "fail" and r.details:
                notes_bits.append(f"{lane}: {r.details[:80]}")
        row_cells.append("; ".join(notes_bits) if notes_bits else "—")
        lines.append("| " + " | ".join(row_cells) + " |")
    lines.append("")
    return "\n".join(lines)


# Skip details emitted by ``run_scenario`` when a scenario simply
# doesn't opt into a lane — this is a deliberate config choice (see
# scenarios.yaml's ``lanes:`` list), not a missing-dep skip, so it
# shouldn't generate annotation noise. The runner emits exactly this
# string; centralising it here keeps the annotation filter in lock-
# step with the producer if either is renamed.
_LANE_OPTOUT_DETAILS_PREFIX = "scenario does not opt into lane"


def _emit_github_annotations(results: list[LaneResult]) -> None:
    """Print ``::warning::`` lines for each skipped lane / sub-check.

    Initiative #9: pre-#9, lane C (compose-up) would silently SKIP
    when ``docker`` wasn't on PATH; lane B (verify) would silently
    skip the frontend build when ``npm`` wasn't on PATH; the
    OpenAPI sub-check in smoke_contract treats 404-on-every-path
    as a skip (currently the Node + Rust template surface). A "green"
    nightly grid hid all three, so a regression that REMOVED a tool
    from the CI image would coast through Lane C as a pass-because-
    skipped without surfacing the loss of coverage.

    GitHub Actions parses ``::warning::`` lines on stdout and renders
    them on the run summary + a per-line annotation. The lane
    keeps its ``status="skip"`` (this isn't a failure — the missing
    tool is environmental), but the warning forces a human eyeball.

    Annotations are NOT emitted for the "scenario does not opt into
    lane X" skip path — that's a deliberate config choice, not a
    missing-dep signal, and treating every cross-axis cell of the
    scenarios x lanes grid as a "skip warning" would drown the real
    missing-dep skips in noise.

    Emitted to stdout (not stderr) because GitHub's annotation
    parser reads stdout; falling back to stderr would land the
    message in the log but not the annotations panel.

    Always called from :func:`main` — local invocations also see the
    warnings, which is fine: a developer running on a machine without
    docker should see exactly the same skip surface CI would record.
    """
    for r in results:
        if (
            r.status == "skip"
            and r.details
            and not r.details.startswith(_LANE_OPTOUT_DETAILS_PREFIX)
        ):
            print(
                f"::warning title=Matrix lane {r.lane!s} skipped ({r.scenario!s})::"
                f"{r.scenario} / {r.lane}: {r.details}"
            )
        for sub in r.skipped_subchecks:
            print(
                f"::warning title=Matrix sub-check skipped ({r.scenario!s} / {r.lane!s})::"
                f"{r.scenario} / {r.lane}: sub-check skipped — {sub}"
            )


def _format_skip_summary(results: list[LaneResult]) -> str:
    """Render a one-line summary of missing-dep skipped lanes + sub-checks.

    Mirrors the annotation filter in :func:`_emit_github_annotations`:
    counts only environmental skips, NOT the "scenario does not opt
    into lane X" routine non-application. The same information is
    embedded as ``::warning::`` annotations elsewhere (visible in the
    GH Actions UI); this summary line is the plain-text receipt for
    local invocations + log greppability.
    """
    skipped_lanes = sum(
        1
        for r in results
        if r.status == "skip"
        and r.details
        and not r.details.startswith(_LANE_OPTOUT_DETAILS_PREFIX)
    )
    skipped_subchecks = sum(len(r.skipped_subchecks) for r in results)
    if not skipped_lanes and not skipped_subchecks:
        return ""
    parts: list[str] = []
    if skipped_lanes:
        parts.append(f"{skipped_lanes} lane(s) skipped")
    if skipped_subchecks:
        parts.append(f"{skipped_subchecks} sub-check(s) skipped")
    return "  [SKIP-SUMMARY] " + ", ".join(parts) + " (see ::warning:: lines above)"


def main() -> int:
    args = _parse_args()
    try:
        scenarios = load_scenarios()
    except ValueError as e:
        print(f"scenarios.yaml is invalid: {e}", file=sys.stderr)
        return 3

    if args.list:
        print(f"{'Name':<24s} {'Lanes':<30s} {'Ports':<10s}  Description")
        for sc in scenarios:
            print(
                f"{sc.name:<24s} {','.join(sc.lanes):<30s} "
                f"{sc.port_base}-{sc.port_base + 9:<5d} {sc.description}"
            )
        return 0

    if args.scenario:
        picked = [sc for sc in scenarios if sc.name == args.scenario]
        if not picked:
            print(f"Unknown scenario: {args.scenario}", file=sys.stderr)
            return 3
        selected = picked
    elif args.all:
        selected = scenarios
    else:
        print("Specify --all, --scenario NAME, or --list", file=sys.stderr)
        return 3

    results = [run_scenario(sc, args.lane) for sc in selected]
    payload = json.dumps(
        [
            {
                "scenario": r.scenario,
                "lane": r.lane,
                "status": r.status,
                "duration_ms": r.duration_ms,
                "details": r.details,
                "missing_files": r.missing_files,
                "skipped_subchecks": r.skipped_subchecks,
            }
            for r in results
        ],
        indent=2,
    )
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(payload + "\n", encoding="utf-8")
    if args.json:
        print(payload)
    elif args.report == "markdown":
        print(_format_markdown_grid(results, selected))
    else:
        for r in results:
            print(_format_row(r))

    # Initiative #9 — emit GH Actions ``::warning::`` annotations for
    # each lane skip / sub-check skip. Plus a one-line plaintext
    # summary so local invocations (no GHA parser) still see the
    # count.
    _emit_github_annotations(results)
    summary = _format_skip_summary(results)
    if summary:
        print(summary)

    failed = [r for r in results if r.status == "fail"]
    return 2 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
