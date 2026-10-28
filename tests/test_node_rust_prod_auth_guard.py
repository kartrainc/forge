"""Fail-closed PRODUCTION auth-config validation for Node and Rust templates.

These are forge-level STRUCTURAL source-assertion tests (the same style as
``tests/test_security_config_guards.py`` and ``tests/test_features_auth_gatekeeper.py``).
forge's CI has no Node/cargo toolchain and ``npm install`` fails in the
sandbox, so we cannot run the generated services' own vitest/cargo suites
here; behavioural verification of the guards is e2e-deferred. Instead we read
the edited template source and assert the guard exists, mirrors the Python
WS-2.1 scope/exemptions, and is wired into the config load path.

Parity target — ``SecurityConfig._reject_default_secret_in_prod`` in
``forge/templates/services/python-service-template/template/src/app/core/config/domain.py``:
  * resolves the effective env (``ENV`` / ``ENVIRONMENT`` / ``NODE_ENV`` /
    ``APP_ENV``) with a PRODUCTION fallback when unset;
  * EXEMPTS exactly {development, dev, local, test, testing};
  * fails closed when the env is production-like AND auth is enabled.

For Node/Rust the auth MIDDLEWARE reads the OIDC issuer/audience from the
``GATEKEEPER_ISSUER`` / ``SERVICE_AUDIENCE`` env vars (NOT the loaded config —
see ``forge/features/auth/templates/platform_auth_{node,rust}_middleware``), so
the config-layer guard validates those env vars directly.

The guard runs inside the config LOAD path (Node: ``loadConfig`` in loader.ts;
Rust: ``AppConfig::load`` -> ``load_with`` -> ``validate``) using the resolved
env/processEnv — NOT global process.env — so it honours the loader's own env
resolution and test hooks. The generated Rust ``main.rs`` must actually CALL
``AppConfig::load`` at startup, or the guard would be dead code.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATES = _REPO_ROOT / "forge" / "templates" / "services"

_NODE_SCHEMA = (
    _TEMPLATES / "node-service-template" / "template" / "src" / "config" / "schema.ts.jinja"
)
_NODE_LOADER = _TEMPLATES / "node-service-template" / "template" / "src" / "config" / "loader.ts"
_RUST_CONFIG = _TEMPLATES / "rust-service-template" / "template" / "src" / "config.rs"
_RUST_MAIN = _TEMPLATES / "rust-service-template" / "template" / "src" / "main.rs"

_RUST_DOCKERFILE = _TEMPLATES / "rust-service-template" / "template" / "Dockerfile.jinja"
_NODE_DOCKERFILE = _TEMPLATES / "node-service-template" / "template" / "Dockerfile.jinja"
_PYTHON_DOCKERFILE = (
    _TEMPLATES / "python-service-template" / "template" / "Dockerfile.jinja"
)

# The five env names that exempt the check, matching Python WS-2.1 exactly.
_EXEMPT = ("development", "dev", "local", "test", "testing")


class TestNodeProdAuthGuard:
    """Node: the guard lives in ``loadConfig`` (loader.ts) so it reads the
    resolved ``processEnv``/``env`` (honouring the options hooks), and fails
    closed when production-like + auth.enabled and the middleware env vars are
    missing."""

    def test_loader_file_exists(self):
        assert _NODE_LOADER.exists(), _NODE_LOADER

    def test_guard_lives_in_loader_not_schema(self):
        """The guard must NOT be a zod superRefine reading global process.env;
        it belongs in loadConfig where the resolved processEnv is available."""
        schema = _NODE_SCHEMA.read_text(encoding="utf-8")
        loader = _NODE_LOADER.read_text(encoding="utf-8")
        assert "superRefine" not in schema, (
            "guard must move out of schema.superRefine (it read global "
            "process.env instead of the loader's resolved processEnv)"
        )
        assert "assertProdAuthConfigured" in loader, (
            "loadConfig must run a prod-auth guard function"
        )

    def test_production_fallback_present(self):
        """When ENV/NODE_ENV is unset the guard must default to production
        (fail-closed), exactly like Python's os.getenv(... "production")."""
        src = _NODE_LOADER.read_text(encoding="utf-8")
        assert "production" in src
        assert "ENV" in src and "NODE_ENV" in src

    def test_uses_resolved_processenv_not_global(self):
        """The guard must read the resolved processEnv (the loader's local /
        options.processEnv), not global process.env, so options hooks work."""
        loader = _NODE_LOADER.read_text(encoding="utf-8")
        guard = loader.split("function assertProdAuthConfigured", 1)
        assert len(guard) == 2, "assertProdAuthConfigured guard function missing"
        body = guard[1].split("\nexport function loadConfig", 1)[0]
        assert "processEnv" in body, "guard must consult the resolved processEnv"
        assert "process.env" not in body, (
            "guard must NOT read global process.env — use the resolved processEnv"
        )

    def test_exempt_env_list_present(self):
        src = _NODE_LOADER.read_text(encoding="utf-8")
        for name in _EXEMPT:
            assert name in src, f"exempt env {name!r} missing from Node guard"

    def test_validates_middleware_env_vars(self):
        src = _NODE_LOADER.read_text(encoding="utf-8")
        assert "GATEKEEPER_ISSUER" in src
        assert "SERVICE_AUDIENCE" in src

    def test_guard_keyed_on_auth_enabled(self):
        src = _NODE_LOADER.read_text(encoding="utf-8")
        assert "auth.enabled" in src

    def test_guard_invoked_in_loadconfig(self):
        """loadConfig must call the guard on every load (after parse)."""
        loader = _NODE_LOADER.read_text(encoding="utf-8")
        load_body = loader.split("export function loadConfig", 1)[1]
        assert "assertProdAuthConfigured(" in load_body


class TestRustProdAuthGuard:
    """Rust: ``AppConfig::validate(env)`` is called at the end of ``load_with``
    (with the resolved env), and ``main.rs`` actually calls ``AppConfig::load``
    at startup so the guard is not dead code."""

    def test_config_file_exists(self):
        assert _RUST_CONFIG.exists(), _RUST_CONFIG

    def test_validate_fn_takes_env_param(self):
        """validate must receive the resolved env (not recompute from globals),
        so it stays consistent with the profile load_with actually selected."""
        src = _RUST_CONFIG.read_text(encoding="utf-8")
        assert "fn validate(&self, env: &str)" in src, (
            "validate must take the resolved env as a parameter"
        )

    def test_validate_returns_configerror(self):
        src = _RUST_CONFIG.read_text(encoding="utf-8")
        block = src.split("fn validate(&self, env: &str)", 1)[1].split("\n    pub fn ", 1)[0]
        assert "ConfigError" in block

    def test_exempt_env_list_present(self):
        src = _RUST_CONFIG.read_text(encoding="utf-8")
        block = src.split("fn validate(&self, env: &str)", 1)[1].split("\n    pub fn ", 1)[0]
        for name in _EXEMPT:
            assert name in block, f"exempt env {name!r} missing from Rust guard"

    def test_validates_middleware_env_vars(self):
        src = _RUST_CONFIG.read_text(encoding="utf-8")
        block = src.split("fn validate(&self, env: &str)", 1)[1].split("\n    pub fn ", 1)[0]
        assert "GATEKEEPER_ISSUER" in block
        assert "SERVICE_AUDIENCE" in block

    def test_guard_keyed_on_auth_enabled(self):
        src = _RUST_CONFIG.read_text(encoding="utf-8")
        block = src.split("fn validate(&self, env: &str)", 1)[1].split("\n    pub fn ", 1)[0]
        assert "self.security.auth.enabled" in block or "auth.enabled" in block

    def test_load_with_passes_resolved_env(self):
        """load_with must call validate with a resolved env (production fallback),
        not let validate recompute from globals."""
        src = _RUST_CONFIG.read_text(encoding="utf-8")
        load_with = src.split("pub fn load_with", 1)[1].split("\n    fn validate", 1)[0]
        assert "production" in load_with, (
            "load_with must resolve the guard env with a production fallback"
        )
        assert ".validate(" in load_with, "load_with must call validate(env)"

    def test_db_url_has_serde_default(self):
        """db.url must have a serde default so AppConfig::load() succeeds in a
        configless container (the startup load runs the guard)."""
        src = _RUST_CONFIG.read_text(encoding="utf-8")
        assert "default_db_url" in src, (
            "DbConfig.url needs a serde default so load() doesn't fail when "
            "config/ is absent at runtime"
        )

    def test_main_calls_appconfig_load(self):
        """The High regression guard: main.rs must actually load+validate config
        at startup, or validate() is dead code (masked by #![allow(dead_code)])."""
        src = _RUST_MAIN.read_text(encoding="utf-8")
        assert "config::AppConfig::load()" in src, (
            "main.rs must call AppConfig::load() at startup so the fail-closed "
            "auth guard runs"
        )
        assert "process::exit(1)" in src, (
            "main.rs must fail closed (non-zero exit) on a config/guard error"
        )


class TestDockerfileEnvParity:
    """All three production service images must set the runtime env explicitly
    AND ship their config/ profiles."""

    def test_python_dockerfile_sets_prod_env(self):
        src = _PYTHON_DOCKERFILE.read_text(encoding="utf-8")
        assert "ENV ENV=production" in src

    def test_node_dockerfile_sets_prod_env(self):
        src = _NODE_DOCKERFILE.read_text(encoding="utf-8")
        assert "ENV NODE_ENV=production" in src

    def test_rust_dockerfile_sets_prod_env(self):
        """Regression guard for the parity gap: the Rust image must pin the
        runtime env so it does not default to the development profile."""
        src = _RUST_DOCKERFILE.read_text(encoding="utf-8")
        assert "ENV ENV=production" in src, (
            "Rust Dockerfile must set ENV=production (else the container loads "
            "the development profile and fails open with auth disabled)"
        )

    def test_rust_dockerfile_copies_config(self):
        """The startup AppConfig::load() resolves config/<ENV>.yaml relative to
        the workdir, so the runtime image must ship config/ (parity with the
        Python and Node images)."""
        src = _RUST_DOCKERFILE.read_text(encoding="utf-8")
        assert "COPY config" in src, (
            "Rust runtime stage must COPY config/ so the prod profile exists "
            "at runtime (matches Python/Node)"
        )


class TestRustComposeDevEnv:
    """The Rust image pins ENV=production and main.rs now runs the fail-closed
    auth guard at startup. So the local compose stacks MUST override ENV to a
    dev value, or `docker compose up` loads production.yaml (auth.enabled=true)
    and the guard aborts boot because compose sets no GATEKEEPER_ISSUER/
    SERVICE_AUDIENCE. Node/Python compose set a dev env; Rust must match."""

    _RUST_COMPOSE = (
        _TEMPLATES / "rust-service-template" / "template" / "docker-compose.yaml.jinja"
    )
    _RUST_COMPOSE_FRAGMENT = (
        _TEMPLATES
        / "rust-service-template"
        / "template"
        / "docker-compose.fragment.yaml.jinja"
    )

    def test_standalone_compose_sets_dev_env(self):
        src = self._RUST_COMPOSE.read_text(encoding="utf-8")
        assert "ENV: development" in src, (
            "rust docker-compose.yaml.jinja must set ENV: development so the "
            "local stack does not inherit the image's ENV=production and trip "
            "the fail-closed auth guard"
        )

    def test_fragment_compose_sets_dev_env(self):
        src = self._RUST_COMPOSE_FRAGMENT.read_text(encoding="utf-8")
        # The app service block (not just migrate) must run in development.
        assert "ENV: development" in src, (
            "rust docker-compose.fragment.yaml.jinja must set ENV: development "
            "on the service so a multi-service deploy-up does not trip the "
            "fail-closed auth guard"
        )
