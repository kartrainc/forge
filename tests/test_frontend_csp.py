"""The content-security policy a Vue frontend is served under (``security.csp``).

With the option on (the default), the frontend's ``nginx.conf`` and the Helm
chart's default server block send the defence-in-depth headers with every
response and a nonce-based policy with ``index.html``; the build stamps the
nonce placeholder that nginx replaces per response. With the option off, or on
a frontend other than the Vue template, nothing of it is emitted. The rendered
Vue lanes prove both kits run under the policy without a violation
(``forge.quality.csp``); these tests pin what is rendered.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from forge.config import (
    BackendConfig,
    BackendLanguage,
    FrontendConfig,
    FrontendFramework,
    ProjectConfig,
)
from forge.docker_manager import render_nginx_conf
from forge.frontend_csp import (
    NONCE_PLACEHOLDER,
    content_security_policy,
    csp_enabled,
    security_headers,
)
from forge.generator import generate
from forge.sync.forge_to_project.updater import update_project

# What scripts/serve-csp.mjs reads from nginx.conf.
_SERVED_POLICY = re.compile(r'add_header Content-Security-Policy "([^"]+)"')


def _config(
    framework: FrontendFramework = FrontendFramework.VUE, **options: object
) -> ProjectConfig:
    return ProjectConfig(
        project_name="csp",
        backends=[BackendConfig(name="api", language=BackendLanguage.PYTHON)],
        frontend=FrontendConfig(
            project_name="frontend",
            framework=framework,
            include_chat=False,
            include_openapi=framework == FrontendFramework.FLUTTER,
        ),
        options=dict(options),
    )


@pytest.fixture
def render() -> Iterator:
    roots: list[Path] = []

    def _render(config: ProjectConfig) -> Path:
        roots.append(Path(generate(config, quiet=True, dry_run=True)))
        return roots[-1]

    yield _render
    for root in roots:
        shutil.rmtree(root.parent, ignore_errors=True)


# --- the policy -------------------------------------------------------------


def test_the_policy_admits_scripts_and_styles_by_nonce_only() -> None:
    policy = dict(
        (directive.split(" ", 1) + [""])[:2]
        for directive in content_security_policy(_config()).split("; ")
    )
    assert "'unsafe-inline'" not in str(policy) and "'unsafe-eval'" not in str(policy)
    assert policy["script-src"].split() == [
        "'self'",
        "'nonce-$request_id'",
        "'strict-dynamic'",
        "'report-sample'",
    ]
    assert policy["style-src"].split() == [
        "'self'",
        "'nonce-$request_id'",
        "'report-sample'",
        "https://fonts.googleapis.com",
    ]
    assert policy["font-src"].split() == ["'self'", "data:", "https://fonts.gstatic.com"]
    # No WebSocket wildcard: either scheme alone admits every host, and the
    # template opens no WebSocket (its event streams are fetch requests).
    assert policy["connect-src"].split() == ["'self'"]
    assert policy["frame-ancestors"] == "'none'" and policy["object-src"] == "'none'"
    # Nuxt UI 4.12 writes its temporary palette element through innerHTML in
    # Vue mode, so Trusted Types is not enforced (ADR-011, open point).
    assert "require-trusted-types-for" not in policy


def test_an_external_api_is_admitted_by_its_origin() -> None:
    config = _config(
        **{
            "frontend.api_target.type": "external",
            "frontend.api_target.url": "https://api.example.com/v1",
        }
    )
    connect = next(
        d for d in content_security_policy(config).split("; ") if d.startswith("connect-src")
    )
    assert connect == "connect-src 'self' https://api.example.com"


@pytest.mark.parametrize(
    "config",
    [
        _config(**{"security.csp": False}),
        _config(FrontendFramework.SVELTE),
        _config(FrontendFramework.FLUTTER),
        ProjectConfig(
            project_name="csp",
            backends=[BackendConfig(name="api", language=BackendLanguage.PYTHON)],
        ),
        # The chat's MCP panels frame remote origins the policy would block.
        ProjectConfig(
            project_name="csp",
            backends=[BackendConfig(name="api", language=BackendLanguage.PYTHON)],
            frontend=FrontendConfig(
                project_name="frontend", framework=FrontendFramework.VUE, include_chat=True
            ),
        ),
    ],
    ids=["option-off", "svelte", "flutter", "no-frontend", "vue-with-chat"],
)
def test_nothing_is_served_without_the_option_or_the_vue_template(config: ProjectConfig) -> None:
    assert not csp_enabled(config)
    assert content_security_policy(config) == ""
    assert security_headers(config) == ()


# --- nginx.conf ---------------------------------------------------------------


def test_nginx_sends_the_policy_with_index_html(tmp_path: Path) -> None:
    conf = render_nginx_conf(_config(), tmp_path).read_text(encoding="utf-8")
    server, index = conf.split("location = /index.html {")
    for name, value in security_headers(_config()):
        assert f'add_header {name} "{value}" always;' in server
        assert f'add_header {name} "{value}" always;' in index
    served = _SERVED_POLICY.search(index)
    assert served is not None and served.group(1) == content_security_policy(_config())
    assert "add_header Content-Security-Policy" not in server
    assert 'add_header Cache-Control "no-store" always;' in index
    assert "sub_filter_once off;" in index
    assert f'sub_filter "{NONCE_PLACEHOLDER}" "$request_id";' in index
    # Strict-Transport-Security is the TLS terminator's to send.
    assert "Strict-Transport-Security" not in conf


def test_nginx_without_the_option_is_the_plain_spa_server(tmp_path: Path) -> None:
    conf = render_nginx_conf(_config(**{"security.csp": False}), tmp_path).read_text(
        encoding="utf-8"
    )
    assert conf == (
        "server {\n"
        "    listen 80;\n"
        "    root /usr/share/nginx/html;\n"
        "    index index.html;\n"
        "\n"
        "    location / {\n"
        "        try_files $uri $uri/ /index.html;\n"
        "    }\n"
        "}\n"
    )


# --- the rendered project -------------------------------------------------------


def test_the_default_project_builds_for_the_policy(render) -> None:
    root = render(_config())
    app = root / "apps" / "frontend"
    vite = (app / "vite.config.ts").read_text(encoding="utf-8")
    assert f"html: {{ cspNonce: '{NONCE_PLACEHOLDER}' }}" in vite
    assert "style=" not in (app / "index.html").read_text(encoding="utf-8")
    shell = (app / "src" / "app" / "App.vue").read_text(encoding="utf-8")
    # radix-vue passes the nonce to the <style> elements it inserts.
    assert '<ConfigProvider :nonce="nonce">' in shell
    main = (app / "src" / "app" / "main.ts").read_text(encoding="utf-8")
    assert [line for line in main.splitlines() if line.startswith("import")][0] == (
        "import './providers/csp'"
    )
    assert _SERVED_POLICY.search((app / "nginx.conf").read_text(encoding="utf-8"))
    # The browser suites run against the build under the policy, with the
    # project's authentication setting: each answers the session in the page.
    assert (app / "scripts" / "serve-csp.mjs").is_file()
    assert (app / "playwright.csp.config.ts").is_file()
    assert "export async function mockSession" in (app / "tests/e2e/session.ts").read_text(
        encoding="utf-8"
    )
    for spec in ("application.spec.ts", "crud.ts"):
        text = (app / "tests/e2e" / spec).read_text(encoding="utf-8")
        assert text.index("await mockHealth(page);") < text.index("await mockSession(page);")
    # The standalone snippet, which nothing included, is gone.
    assert not (root / "deploy" / "infra" / "nginx-csp.conf").exists()


def test_the_option_off_builds_without_the_nonce(render) -> None:
    root = render(_config(**{"security.csp": False}))
    app = root / "apps" / "frontend"
    assert "cspNonce" not in (app / "vite.config.ts").read_text(encoding="utf-8")
    assert NONCE_PLACEHOLDER not in (app / "index.html").read_text(encoding="utf-8")
    assert "Content-Security-Policy" not in (app / "nginx.conf").read_text(encoding="utf-8")
    assert not (app / "scripts" / "serve-csp.mjs").exists()
    assert not (app / "playwright.csp.config.ts").exists()


# --- the Helm chart -------------------------------------------------------------


def _chart(render, **options: object) -> Path:
    return render(_config(**{"deploy.target": "kubernetes", **options})) / "deploy" / "helm"


def test_the_chart_values_carry_the_headers_and_the_policy(render) -> None:
    values = (_chart(render) / "values.yaml").read_text(encoding="utf-8")
    assert f'  contentSecurityPolicy: "{content_security_policy(_config())}"' in values
    for name, value in security_headers(_config()):
        assert f'    {name}: "{value}"' in values
    values_off = (_chart(render, **{"security.csp": False}) / "values.yaml").read_text(
        encoding="utf-8"
    )
    assert '  contentSecurityPolicy: ""' in values_off
    assert "  securityHeaders: {}" in values_off


def test_an_update_without_a_recipe_keeps_a_chat_project_without_the_policy(
    render, tmp_path: Path
) -> None:
    config = _config(**{"deploy.target": "kubernetes"})
    assert config.frontend is not None
    config.frontend.include_chat = True
    root = tmp_path / "project"
    shutil.copytree(render(config), root)
    values = root / "deploy" / "helm" / "values.yaml"
    assert '  contentSecurityPolicy: ""' in values.read_text(encoding="utf-8")
    # Without a recipe the update rebuilds the frontend from forge.toml, which
    # does not record the chat; the app's Copier answers do.
    (root / ".forge" / "quality.json").unlink()
    update_project(root, quiet=True, update_mode="merge")
    updated = values.read_text(encoding="utf-8")
    assert '  contentSecurityPolicy: ""' in updated
    assert "  securityHeaders: {}" in updated


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")
@pytest.mark.parametrize("csp", [True, False], ids=["option-on", "option-off"])
def test_the_charts_default_server_sends_what_nginx_conf_sends(render, csp: bool) -> None:
    rendered = subprocess.run(
        ["helm", "template", "rel", str(_chart(render, **{"security.csp": csp}))],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    nginx = rendered.split("default.conf: |", 1)[1].split("\n---", 1)[0]
    if not csp:
        assert "add_header" not in nginx and "sub_filter" not in nginx
        return
    served = _SERVED_POLICY.search(nginx)
    assert served is not None and served.group(1) == content_security_policy(_config())
    assert "location = /index.html {" in nginx
    assert 'sub_filter "__CSP_NONCE__" "$request_id";' in nginx
    assert 'add_header Cache-Control "no-store" always;' in nginx
    for name, value in security_headers(_config()):
        assert nginx.count(f'add_header {name} "{value}" always;') == 2


@pytest.mark.parametrize("edited", [False, True], ids=["untouched", "edited"])
def test_an_update_brings_the_current_serve_script(render, tmp_path: Path, edited: bool) -> None:
    """A project's older serve script is replaced while untouched, kept with a sidecar once edited."""
    from forge.sync.provenance import sha256_of

    root = tmp_path / "project"
    shutil.copytree(render(_config()), root)
    rel = "apps/frontend/scripts/serve-csp.mjs"
    script = root / rel
    older = "// serve-csp.mjs as forge wrote it before the canary\n"
    script.write_text(older, encoding="utf-8")
    manifest = root / "forge.toml"
    recorded = re.compile(
        r'(\[forge\.provenance\."' + re.escape(rel) + r'"\][^\[]*?sha256 = ")[0-9a-f]+'
    )
    assert recorded.search(manifest.read_text(encoding="utf-8"))
    # forge.toml records what forge wrote: the older script.
    manifest.write_text(
        recorded.sub(
            lambda m: m.group(1) + sha256_of(script), manifest.read_text(encoding="utf-8")
        ),
        encoding="utf-8",
    )
    if edited:
        script.write_text(older + "// the project's own edit\n", encoding="utf-8")
    update_project(root, quiet=True, update_mode="merge")
    current = script.read_text(encoding="utf-8")
    sidecar = root / (rel + ".forge-merge")
    if edited:
        assert "the project's own edit" in current and "/__csp-canary" not in current
        assert "/__csp-canary" in sidecar.read_text(encoding="utf-8")
    else:
        assert "/__csp-canary" in current and not sidecar.exists()


# --- scripts/serve-csp.mjs -------------------------------------------------------

_SERVE_CSP = Path(__file__).resolve().parents[1] / "forge/templates/_common/serve-csp.mjs"


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _serve(app: Path, env: dict[str, str]) -> tuple[subprocess.Popen, int]:
    port = _free_port()
    server = subprocess.Popen(
        ["node", "scripts/serve-csp.mjs", str(port)],
        cwd=app,
        env={**os.environ, **env},
        stderr=subprocess.PIPE,
        text=True,
    )
    return server, port


def _get(port: int) -> tuple[str, str]:
    for _ in range(50):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/checkouts", timeout=2) as res:
                return res.headers["Content-Security-Policy"], res.read().decode()
        except OSError:
            time.sleep(0.1)
    raise AssertionError("serve-csp.mjs did not answer")


def _app(tmp_path: Path, nginx: str, files: dict[str, str] | None = None) -> Path:
    """A built app as the check sees it: dist/, nginx.conf, the serve script."""
    app = tmp_path / "frontend"
    (app / "scripts").mkdir(parents=True)
    (app / "dist").mkdir()
    shutil.copy(_SERVE_CSP, app / "scripts" / "serve-csp.mjs")
    (app / "dist" / "index.html").write_text(
        f'<html><head><script type="module" nonce="{NONCE_PLACEHOLDER}" src="/a.js"></script>'
        "</head><body></body></html>",
        encoding="utf-8",
    )
    (app / "nginx.conf").write_text(nginx, encoding="utf-8")
    for name, text in (files or {}).items():
        (app / name).write_text(text, encoding="utf-8")
    return app


_KEPT_POLICY = (
    "add_header Content-Security-Policy \"default-src 'self'; "
    "script-src 'self' 'nonce-$request_id'; img-src 'self' ${MEDIA_ORIGINS}; "
    'report-to csp" always;\n'
)
_INCLUDING = "server {\n    include /etc/nginx/snippets/security-headers.conf;\n}\n"


_CHECK_FILE = json.dumps(
    {"policy": "security-headers.conf", "variables": {"MEDIA_ORIGINS": "https://media.example"}}
)


def _fetch(port: int, path: str) -> tuple[str, str]:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as res:
        return res.headers["Content-Security-Policy"], res.read().decode()


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_the_check_serves_the_policy_file_a_project_names(tmp_path: Path) -> None:
    """A consumer's own header template, as kartra-ai keeps it, is what the check serves."""
    app = _app(
        tmp_path,
        _INCLUDING,
        {"security-headers.conf": _KEPT_POLICY, "csp-check.json": _CHECK_FILE},
    )
    server, port = _serve(app, {})
    try:
        policy, page = _get(port)
        nonce = re.search(r'nonce="([0-9a-f]{32})"', page)
        assert nonce is not None and f"'nonce-{nonce.group(1)}'" in policy
        assert "img-src 'self' https://media.example;" in policy
        # The policy's own report directive gives way to the check's collector:
        # a browser that knows report-to would ignore a report-uri beside it.
        assert "report-to" not in policy and policy.endswith("; report-uri /__csp-report")
        # The app's pages are served untouched; the canary is a page of its own,
        # under a fixed policy that blocks its script whatever the project admits.
        assert "csp-canary" not in page
        canary_policy, canary = _fetch(port, "/__csp-canary")
        assert canary_policy == "default-src 'none'; script-src 'none'; report-uri /__csp-report"
        assert '<script src="/__csp-canary.js"></script>' in canary
    finally:
        server.terminate()
        server.wait(timeout=10)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_a_policy_file_needs_no_nginx_conf(tmp_path: Path) -> None:
    app = _app(tmp_path, "", {"security-headers.conf": _KEPT_POLICY, "csp-check.json": _CHECK_FILE})
    (app / "nginx.conf").unlink()
    server, port = _serve(app, {})
    try:
        assert "img-src 'self' https://media.example;" in _get(port)[0]
    finally:
        server.terminate()
        server.wait(timeout=10)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_without_chromium_the_canary_says_so_and_the_server_still_serves(tmp_path: Path) -> None:
    # The app has no node_modules, so @playwright/test cannot be imported.
    app = _app(tmp_path, f'add_header Content-Security-Policy "{"default-src 'self'"}" always;\n')
    server, port = _serve(app, {"FORGE_CSP_REPORT": str(tmp_path / "reports.jsonl")})
    try:
        assert _get(port)[0].startswith("default-src 'self'")
    finally:
        server.terminate()
        _, stderr = server.communicate(timeout=10)
    assert "could not open the canary page in Chromium" in stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize(
    ("nginx", "files", "error"),
    [
        (_INCLUDING, {}, "neither nginx.conf nor a file csp-check.json names"),
        (
            _INCLUDING,
            {
                "security-headers.conf": _KEPT_POLICY,
                "csp-check.json": json.dumps({"policy": "security-headers.conf"}),
            },
            "uses ${MEDIA_ORIGINS}, which csp-check.json does not declare",
        ),
        (
            "server {\n"
            "    add_header Content-Security-Policy \"default-src 'self'\" always;\n"
            "    add_header Content-Security-Policy \"default-src 'none'\" always;\n}\n",
            {},
            "sends 2 different Content-Security-Policy headers",
        ),
        (
            "server {\n    add_header Content-Security-Policy \"default-src 'self'\" always;\n}\n",
            {"security-headers.conf": _KEPT_POLICY, "csp-check.json": _CHECK_FILE},
            "csp-check.json names another source: keep one",
        ),
    ],
    ids=["no-source", "undeclared-variable", "two-policies", "two-sources"],
)
def test_the_check_refuses_a_policy_it_cannot_pin_down(
    tmp_path: Path, nginx: str, files: dict[str, str], error: str
) -> None:
    app = _app(tmp_path, nginx, files)
    done = subprocess.run(
        ["node", "scripts/serve-csp.mjs", "0"], cwd=app, capture_output=True, text=True, timeout=30
    )
    assert done.returncode != 0 and error in done.stderr
