"""End-to-end: the dev-server overlay hot-reloads a generated Vue frontend (ADR-013).

Generates a project with ``frontend.dev_server`` on, builds the ``dev`` image and starts
the overlay with ``docker compose up --watch``, then drives Chromium through Traefik:
an edit to a component on the host must reach the page without a reload, and nothing the
container writes may be owned by anyone but the developer.

Runtime coverage is npm with the shadcn kit and sign-in off. pnpm and yarn are covered by
the render tests: their Docker install step and host generation fail before the dev stage
does (see the plan's risks), so a runtime case for them would not test this feature.

Traefik reads every container on the Docker daemon, so this test constrains its own to the
test's Compose project and publishes it on a free loopback port. Heavy and opt-in (``e2e``);
needs Docker Compose 2.40 or newer (``develop.watch.initial_sync``).

Run explicitly::

    pytest tests/e2e/test_dev_server_hmr.py -m e2e -v -s
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from forge.doctor import MIN_COMPOSE_FOR_DEV_SERVER, parse_compose_version

pytestmark = pytest.mark.e2e

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_BUILD_TIMEOUT = 1500
_HEALTH_WAIT = 300

# A paragraph of the generated home page, which renders without signing in.
_ANCHOR = "Here's what's happening with your service"
_HOME = "src/features/home/ui/HomePage.vue"

_STACK = """\
project_name: devhmr
description: "dev server hot reload"
include_keycloak: false
backends:
  - { name: api, language: python, python_version: "3.13", app_template: crud-service, features: [items], server_port: 5010 }
frontend: { framework: vue, include_auth: false, include_chat: false, package_manager: npm, server_port: 5173, dev_server: true }
"""

# Runs in the generated app, so its own @playwright/test resolves. Prints one JSON line.
_PROBE = r"""
import { createRequire } from 'node:module'
import { readFileSync, writeFileSync } from 'node:fs'
const require = createRequire(process.argv[2] + '/package.json')
const { chromium } = require('@playwright/test')
const [, , , url, file, anchor] = process.argv
const original = readFileSync(file, 'utf8')
if (!original.includes(anchor)) {
  console.log(JSON.stringify({ error: 'anchor text not found in ' + file }))
  process.exit(0)
}
const marker = ' [HMR-PROBE]'
const result = {}
const browser = await chromium.launch()
try {
  const page = await (await browser.newContext()).newPage()
  let navigations = 0
  const errors = []
  page.on('framenavigated', (f) => { if (f === page.mainFrame()) navigations += 1 })
  page.on('pageerror', (e) => errors.push(String(e).slice(0, 200)))
  await page.goto(url, { waitUntil: 'networkidle', timeout: 90000 })
  await page.getByText(anchor).first().waitFor({ timeout: 30000 })
  await page.evaluate(() => { window.__marker = 'still-here' })
  const before = navigations
  const started = Date.now()
  writeFileSync(file, original.replace(anchor, anchor + marker))
  await page.getByText(anchor + marker).first().waitFor({ timeout: 30000 })
  result.updatedMs = Date.now() - started
  result.markerKept = (await page.evaluate(() => window.__marker)) === 'still-here'
  result.navigations = navigations - before
  result.errors = errors
} catch (e) {
  result.error = String(e).slice(0, 400)
} finally {
  writeFileSync(file, original)
  await browser.close()
}
console.log(JSON.stringify(result))
"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _run(cmd: list[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    assert proc.returncode == 0, (
        f"{' '.join(cmd)} failed:\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
    )
    return proc


def _statuses(compose: list[str], root: Path) -> dict[str, str]:
    out = subprocess.run(
        [*compose, "ps", "--format", "json"], cwd=root, capture_output=True, text=True, timeout=60
    ).stdout
    found: dict[str, str] = {}
    for line in out.splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        found[rec.get("Service", "")] = rec.get("Health") or rec.get("State") or ""
    return found


def _http_status(compose: list[str], root: Path, host_header: str) -> str:
    """Status the dev server answers with for ``Host: <host_header>``, asked from inside."""
    script = (
        "require('http').get({host:'127.0.0.1',port:5173,headers:{Host:'"
        + host_header
        + "'}},r=>{console.log(r.statusCode);r.resume()})"
    )
    out = subprocess.run(
        [*compose, "exec", "-T", "frontend", "node", "-e", script],
        cwd=root, capture_output=True, text=True, timeout=60,
    )  # fmt: skip
    return out.stdout.strip()


def _wait(what: str, ready, log: Path, timeout: int) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ready():
            return
        time.sleep(4)
    raise AssertionError(f"{what} not reached in {timeout}s:\n{log.read_text()[-3000:]}")


def test_dev_server_hot_reloads_a_component_through_traefik(
    tmp_path: Path, has_docker: bool, has_npm: bool
) -> None:
    if not (has_docker and has_npm):
        pytest.skip("needs Docker and npm")
    version = subprocess.run(
        ["docker", "compose", "version", "--short"], capture_output=True, text=True, timeout=30
    ).stdout
    have = parse_compose_version(version)
    assert have is not None and have >= MIN_COMPOSE_FOR_DEV_SERVER, (
        f"Docker Compose {version.strip()!r} cannot run the dev overlay (needs 2.40+)"
    )

    stack = tmp_path / "stack.yaml"
    stack.write_text(_STACK, encoding="utf-8")
    _run(
        [
            sys.executable,
            "-m",
            "forge",
            "--config",
            str(stack),
            "--output-dir",
            str(tmp_path / "out"),
        ]
        + ["--yes", "--no-docker"],
        _REPO_ROOT,
        900,
    )
    root = tmp_path / "out" / "devhmr"
    app = root / "apps" / "frontend"
    assert (root / "docker-compose.dev.yaml").is_file()
    # A project config that admits every host must not widen what the dev server admits.
    vite_config = app / "vite.config.ts"
    original_config = vite_config.read_text(encoding="utf-8")
    widened = original_config.replace("  server: {\n", "  server: {\n    allowedHosts: true,\n", 1)
    assert widened != original_config, "the template's server block moved"
    vite_config.write_text(widened, encoding="utf-8")

    project = f"forgedev{uuid.uuid4().hex[:8]}"
    port = _free_port()
    (root / "compose.e2e.yaml").write_text(
        "services:\n"
        "  traefik:\n"
        f'    ports: !override\n      - "127.0.0.1:{port}:80"\n'
        "    environment:\n"
        "      TRAEFIK_PROVIDERS_DOCKER_CONSTRAINTS: "
        f'"Label(`com.docker.compose.project`,`{project}`)"\n',
        encoding="utf-8",
    )
    compose = [
        "docker", "compose", "-p", project,
        "-f", "docker-compose.yml", "-f", "docker-compose.dev.yaml", "-f", "compose.e2e.yaml",
    ]  # fmt: skip
    log = (tmp_path / "up.log").open("w")
    watcher = subprocess.Popen(
        [*compose, "up", "--build", "--watch", "--no-deps", "frontend", "traefik"],
        cwd=root,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.monotonic() + _BUILD_TIMEOUT + _HEALTH_WAIT
        while time.monotonic() < deadline:
            assert watcher.poll() is None, (
                f"compose up exited early:\n{(tmp_path / 'up.log').read_text()[-3000:]}"
            )
            if _statuses(compose, root).get("frontend") == "healthy":
                break
            time.sleep(5)
        else:
            raise AssertionError(
                f"frontend not healthy:\n{(tmp_path / 'up.log').read_text()[-3000:]}"
            )
        up_log = tmp_path / "up.log"
        healthy = lambda: _statuses(compose, root).get("frontend") == "healthy"  # noqa: E731

        # Only the routed host is admitted, whatever the project's own config says.
        assert _http_status(compose, root, "app.localhost") == "200"
        assert _http_status(compose, root, "evil.example") == "403"

        _run(["npx", "--no-install", "playwright", "install", "chromium"], app, 900)
        probe = tmp_path / "probe.mjs"
        probe.write_text(_PROBE, encoding="utf-8")
        ran = _run(
            [
                "node",
                str(probe),
                str(app),
                f"http://app.localhost:{port}/",
                str(app / _HOME),
                _ANCHOR,
            ],
            app,
            300,
        )
        result = json.loads(ran.stdout.strip().splitlines()[-1])
        assert "error" not in result, result
        assert result["markerKept"] is True, "the page reloaded instead of hot-updating"
        assert result["navigations"] == 0, result
        assert result["errors"] == [], result
        assert result["updatedMs"] < 30_000

        # The dev server runs as an unprivileged user and writes nothing to the checkout
        # as anyone but the developer.
        user = subprocess.run(
            [*compose, "exec", "-T", "frontend", "id", "-un"],
            cwd=root, capture_output=True, text=True, timeout=60,
        ).stdout.strip()  # fmt: skip
        assert user == "node"
        foreign = [
            str(p.relative_to(root))
            for p in root.rglob("*")
            if "node_modules" not in p.parts and p.lstat().st_uid != os.getuid()
        ]
        assert foreign == []
        assert (app / _HOME).read_text(encoding="utf-8").count("[HMR-PROBE]") == 0
        writable = subprocess.run(
            [*compose, "exec", "-T", "frontend", "sh", "-c"]
            + ["test -w /app && test -w /app/node_modules/.vite && test -w /app/node_modules/.vite-temp"],
            cwd=root, capture_output=True, timeout=60,
        )  # fmt: skip
        assert writable.returncode == 0, "Vite's cache and the app directory must be writable"

        # A dependency change rebuilds the image and replaces the container.
        def container() -> str:
            return subprocess.run(
                [*compose, "ps", "-q", "frontend"], cwd=root, capture_output=True, text=True
            ).stdout.strip()

        before = container()
        package = app / "package.json"
        manifest = json.loads(package.read_text(encoding="utf-8"))
        manifest["description"] = "rebuild probe"
        package.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

        def replaced() -> bool:
            now = container()
            return bool(now) and now != before and healthy()

        _wait("a rebuilt, healthy frontend", replaced, up_log, 600)
        shown = subprocess.run(
            [*compose, "exec", "-T", "frontend", "cat", "/app/package.json"],
            cwd=root, capture_output=True, text=True, timeout=60,
        ).stdout  # fmt: skip
        assert "rebuild probe" in shown
    finally:
        watcher.terminate()
        try:
            watcher.wait(timeout=60)
        except subprocess.TimeoutExpired:
            watcher.kill()
        log.close()
        subprocess.run(
            [*compose, "down", "-v", "--remove-orphans", "--rmi", "local"],
            cwd=root, capture_output=True, timeout=180, check=False,
        )  # fmt: skip
