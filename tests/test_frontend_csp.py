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

import re
import shutil
import subprocess
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
    assert policy["connect-src"].split() == ["'self'", "ws:", "wss:"]
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
    assert connect == "connect-src 'self' https://api.example.com ws: wss:"


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
    # The browser suites run against the build under the policy.
    assert (app / "scripts" / "serve-csp.mjs").is_file()
    assert (app / "playwright.csp.config.ts").is_file()
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
