"""Phase B1 — ``database.mode=none`` scenario tests.

Exercises the database-layer discriminator. ``database.mode=none``
strips the postgres container + per-backend migrate sidecars from
``docker-compose.yml`` — suitable for stateless services or projects
whose persistence lives outside the generated stack.

Phase B1 scope is **compose-level only**: the Python service template
still scaffolds alembic + SQLAlchemy. A follow-up ``database_strip``
fragment will remove those files too (tracked separately). The B1
validation rejects DB-backed options (``conversation.persistence``,
``rag.backend != none``, etc.) so users can't generate a broken
combination.
"""

from __future__ import annotations

import pytest
import yaml

from forge.config import (
    BackendConfig,
    FrontendConfig,
    FrontendFramework,
    ProjectConfig,
)
from forge.docker_manager import render_compose


def _stateless_config(**options_overrides: object) -> ProjectConfig:
    """Return a Python backend + Vue frontend with ``database.mode=none``."""
    bc = BackendConfig(
        project_name="Stateless",
        name="api",
        features=["events"],
        server_port=5000,
    )
    fc = FrontendConfig(
        framework=FrontendFramework.VUE,
        project_name="Stateless",
        features=["events"],
        server_port=5173,
        include_auth=False,
        include_openapi=False,
        keycloak_url="http://localhost:8080",
        keycloak_realm="master",
        keycloak_client_id="stateless",
    )
    options: dict[str, object] = {"database.mode": "none"}
    options.update(options_overrides)
    return ProjectConfig(
        project_name="Stateless",
        backends=[bc],
        frontend=fc,
        options=options,
    )


# -- Config validation -------------------------------------------------------


class TestDatabaseModeValidation:
    def test_stateless_backend_validates(self):
        config = _stateless_config()
        config.validate()
        assert config.database_mode == "none"

    def test_default_mode_is_generate(self):
        bc = BackendConfig(project_name="default", server_port=5000)
        config = ProjectConfig(project_name="Default", backends=[bc])
        config.validate()
        assert config.database_mode == "generate"

    def test_stateless_frees_postgres_host_port(self):
        # With database.mode=none and no Keycloak, no Postgres service renders,
        # so the 15432/5050 host binds are free — a backend may use them.
        config = _stateless_config()
        config.backends[0].server_port = 15432
        config.frontend = None
        config.validate()  # must not raise

    def test_generate_mode_reserves_postgres_host_port(self):
        # Sanity: when Postgres DOES render, 15432 is reserved.
        bc = BackendConfig(project_name="default", name="api", server_port=15432)
        config = ProjectConfig(project_name="Default", backends=[bc])
        with pytest.raises(ValueError, match="PostgreSQL"):
            config.validate()

    @pytest.mark.parametrize(
        ("option_key", "option_value", "error_fragment"),
        [
            ("conversation.persistence", True, "conversation.persistence"),
            ("rag.backend", "qdrant", "rag.backend"),
            ("chat.attachments", True, "chat.attachments"),
            ("agent.streaming", True, "agent.streaming"),
            ("agent.llm", True, "agent.llm"),
            ("platform.admin", True, "platform.admin"),
            ("platform.webhooks", True, "platform.webhooks"),
            # Cluster D guards (matrix-nightly-fixes plan). Cluster D
            # reorders the generator pipeline so strip_python_database
            # runs BEFORE fragment application. That depends on the
            # validator rejecting every DB-consuming fragment combo —
            # events.outbox ships an alembic migration, events.bus=
            # postgres_notify uses postgres pub/sub, streaming.sse needs
            # events.bus != none (transitively DB), and airlock.client
            # backs its state in DB.
            ("events.outbox", True, "events.outbox"),
            ("events.bus", "postgres_notify", "events.bus"),
            ("streaming.sse", True, "streaming.sse"),
            ("airlock.client", True, "airlock.client"),
            # Init #7 codex follow-up: these options enable fragments
            # that transitively pull conversation_persistence (DB-backed)
            # but the original validate() didn't list them. The
            # metadata-driven walker now catches them via the
            # requires_database flags landed in the same commit set.
            ("agent.mode", "llm_only", "agent.mode"),
            ("agent.mode", "tool_calling", "agent.mode"),
            ("rag.embeddings", "voyage", "rag.embeddings"),
            ("rag.reranker", True, "rag.reranker"),
            ("async.rag_ingest_queue", True, "async.rag_ingest_queue"),
        ],
    )
    def test_mode_none_rejects_db_backed_options(
        self, option_key, option_value, error_fragment
    ):
        config = _stateless_config(**{option_key: option_value})
        with pytest.raises(ValueError, match=error_fragment):
            config.validate()

    def test_mode_none_allows_events_bus_none(self):
        """``events.bus=none`` is the default — stateless mode shouldn't reject it.

        Only the DB-requiring value (``postgres_notify`` today) conflicts;
        an explicit ``none`` value should be a no-op for the validator.
        """
        config = _stateless_config(**{"events.bus": "none"})
        config.validate()  # should not raise

    def test_mode_none_lists_all_conflicts(self):
        """The error names every conflicting option in one shot so the
        user can fix them together rather than re-running the generator."""
        config = _stateless_config(
            **{"conversation.persistence": True, "platform.admin": True}
        )
        with pytest.raises(ValueError) as exc:
            config.validate()
        msg = str(exc.value)
        assert "conversation.persistence" in msg
        assert "platform.admin" in msg

    def test_rag_backend_none_is_fine(self):
        """``rag.backend=none`` is the default; stateless mode shouldn't
        reject it."""
        config = _stateless_config(**{"rag.backend": "none"})
        config.validate()  # should not raise

    def test_mode_generate_allows_db_backed_options(self):
        """The B1 validator only fires when ``database.mode=none``; the
        default generate mode must stay permissive."""
        config = _stateless_config()
        config.options["database.mode"] = "generate"
        config.options["conversation.persistence"] = True
        # conversation.persistence has its own downstream requirements,
        # but database.mode=generate shouldn't reject it.
        config.validate()


class TestStatelessPiiRedactionSurvivesStrip:
    """Cluster D functional guard (matrix-nightly-fixes plan).

    Stateless generation must preserve the default-enabled pii_redaction
    fragment's injection into ``lifecycle.py``. Pre-Cluster-D the
    generator ran fragments BEFORE the stripper, so the stripper would
    overwrite the just-injected ``install_pii_filter()`` call without any
    signal — silently disabling PII redaction in production logs.

    Cluster D's pipeline reorder runs the stripper first; this test pins
    that the resulting lifecycle.py contains BOTH the import and the call
    that pii_redaction's inject.yaml emits, so a future re-shuffle of the
    pipeline can't silently regress the security guarantee.
    """

    def test_lifecycle_contains_install_pii_filter_after_strip(
        self, tmp_path: Path
    ):
        from forge.generator import generate

        bc = BackendConfig(
            name="api",
            project_name="StatelessPii",
            features=["items"],
            server_port=5000,
        )
        config = ProjectConfig(
            project_name="StatelessPii",
            backends=[bc],
            options={
                "database.mode": "none",
                # pii_redaction defaults to True (see
                # forge/features/middleware/options.py); we set it
                # explicitly so this test pins the security guarantee
                # even if the default changes later.
                "middleware.pii_redaction": True,
            },
            output_dir=str(tmp_path),
        )
        config.validate()

        # dry_run=True skips toolchain.install (slow) but still runs
        # Copier + fragment application + the stripper, which is what we
        # want to assert about.
        project_root = generate(config, quiet=True, dry_run=True)

        lifecycle = project_root / "services/api/src/app/core/lifecycle.py"
        assert lifecycle.is_file(), f"lifecycle.py not rendered at {lifecycle}"
        text = lifecycle.read_text(encoding="utf-8")
        assert "from app.core.pii_redaction import install_pii_filter" in text, (
            "pii_redaction import was clobbered by the stripper — the generator "
            "pipeline ordering regressed; see Cluster D in "
            "plans/2026-05-19-matrix-nightly-fixes-plan.md"
        )
        assert "install_pii_filter()" in text, (
            "pii_redaction install call was clobbered — security regression "
            "(default-on PII filter is now inert)"
        )


# -- Manifest stamping (#260 generator hardening) ----------------------------


class TestStatelessManifestStamping:
    """#260 Part 2 — the generator must not persist mode-inapplicable layer
    sub-fields into ``forge.toml``.

    The option registry stamps blanket ``database.engine`` /
    ``frontend.api_target.*`` defaults regardless of mode, and the
    generator wrote the fully-defaulted option set into the manifest. That
    produced a ``database.mode=none`` manifest carrying
    ``database.engine=postgres`` — which the strict typed config rejects on
    ``forge update`` (see ``from_legacy_options`` / issue #260). Drop dead
    layer fields at stamp time so new manifests are honest and update-safe.
    """

    def test_stateless_manifest_omits_engine(self, tmp_path):
        from forge.generator import generate
        from forge.sync.manifest import read_forge_toml

        bc = BackendConfig(
            name="api",
            project_name="StatelessManifest",
            features=["items"],
            server_port=5000,
        )
        config = ProjectConfig(
            project_name="StatelessManifest",
            backends=[bc],
            options={"database.mode": "none"},
            output_dir=str(tmp_path),
        )
        config.validate()

        project_root = generate(config, quiet=True, dry_run=True)
        data = read_forge_toml(project_root / "forge.toml")

        assert data.options.get("database.mode") == "none"
        # The dead engine field must not be persisted...
        assert "database.engine" not in data.options
        # ...and it must not leave an orphaned origin entry behind.
        assert "database.engine" not in data.option_origins

    def test_headless_manifest_omits_api_target(self, tmp_path):
        from forge.generator import generate
        from forge.sync.manifest import read_forge_toml

        bc = BackendConfig(
            name="api",
            project_name="HeadlessManifest",
            features=["items"],
            server_port=5000,
        )
        config = ProjectConfig(
            project_name="HeadlessManifest",
            backends=[bc],
            options={"frontend.mode": "none"},
            output_dir=str(tmp_path),
        )
        config.validate()

        project_root = generate(config, quiet=True, dry_run=True)
        data = read_forge_toml(project_root / "forge.toml")

        assert data.options.get("frontend.mode") == "none"
        assert "frontend.api_target.type" not in data.options
        assert "frontend.api_target.url" not in data.options
        assert "frontend.api_target.type" not in data.option_origins
        assert "frontend.api_target.url" not in data.option_origins

    def test_generate_mode_manifest_retains_engine(self, tmp_path):
        """Regression guard: the pop must NEVER touch generate/external
        modes — a default (``database.mode=generate``) project keeps its
        ``database.engine=postgres`` in the manifest."""
        from forge.generator import generate
        from forge.sync.manifest import read_forge_toml

        bc = BackendConfig(
            name="api",
            project_name="GenerateManifest",
            features=["items"],
            server_port=5000,
        )
        config = ProjectConfig(
            project_name="GenerateManifest",
            backends=[bc],
            output_dir=str(tmp_path),
        )
        config.validate()

        project_root = generate(config, quiet=True, dry_run=True)
        data = read_forge_toml(project_root / "forge.toml")

        assert data.options.get("database.mode") == "generate"
        assert data.options.get("database.engine") == "postgres"


# -- Compose rendering -------------------------------------------------------


class TestStatelessCompose:
    def test_postgres_stripped(self, tmp_path):
        config = _stateless_config()
        config.validate()
        compose_path = render_compose(config, tmp_path)
        data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
        assert "postgres" not in data["services"]
        assert "pgadmin" not in data["services"]

    def test_migrate_services_stripped(self, tmp_path):
        config = _stateless_config()
        config.validate()
        compose_path = render_compose(config, tmp_path)
        data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
        assert all(not name.endswith("-migrate") for name in data["services"])

    def test_backend_service_has_no_db_env_vars(self, tmp_path):
        config = _stateless_config()
        config.validate()
        compose_path = render_compose(config, tmp_path)
        data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
        env = data["services"]["api"]["environment"]
        # The Python backend exposes ENV (pydantic-settings) but not the
        # database URL. node/rust would do likewise.
        assert "APP__DB__URL" not in env
        assert "DATABASE_URL" not in env

    def test_backend_service_has_no_migrate_dependency(self, tmp_path):
        """Without a migrate sidecar, the backend service shouldn't list
        one in ``depends_on`` — docker-compose rejects unknown service
        references."""
        config = _stateless_config()
        config.validate()
        compose_path = render_compose(config, tmp_path)
        data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
        depends = data["services"]["api"].get("depends_on") or {}
        assert "api-migrate" not in depends

    def test_volumes_block_absent(self, tmp_path):
        """No postgres → no ``pgdata`` volume. YAML's ``volumes:`` top-level
        key should be absent so docker-compose doesn't complain about an
        empty mapping."""
        config = _stateless_config()
        config.validate()
        compose_path = render_compose(config, tmp_path)
        data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
        assert not (data.get("volumes") or {})


class TestStatelessKeycloakCoexistence:
    """``database.mode=none + include_keycloak=True`` is tricky: keycloak
    needs postgres for its own state, but backends don't. Postgres must
    still render."""

    def test_postgres_renders_for_keycloak(self, tmp_path):
        config = _stateless_config()
        config.backends[0].server_port = 5010  # dodge the gatekeeper 5000 host bind
        config.frontend.include_auth = True
        config.frontend.keycloak_client_id = "stateless"
        config.include_keycloak = True
        config.validate()
        compose_path = render_compose(config, tmp_path)
        data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
        assert "postgres" in data["services"]
        assert "keycloak" in data["services"]

    def test_backend_still_has_no_db_wiring(self, tmp_path):
        """Even with postgres rendered for keycloak, backends stay
        stateless — no APP__DB__URL / DATABASE_URL env vars."""
        config = _stateless_config()
        config.backends[0].server_port = 5010  # dodge the gatekeeper 5000 host bind
        config.frontend.include_auth = True
        config.frontend.keycloak_client_id = "stateless"
        config.include_keycloak = True
        config.validate()
        compose_path = render_compose(config, tmp_path)
        data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
        env = data["services"]["api"]["environment"]
        assert "APP__DB__URL" not in env
        # But auth env vars DO populate since keycloak is enabled.
        assert env["APP__SECURITY__AUTH__ENABLED"] == "true"


# -- Backward compatibility --------------------------------------------------


class TestDefaultMode:
    """``database.mode=generate`` (default) must preserve pre-B1 behavior."""

    def test_postgres_still_renders(self, tmp_path):
        bc = BackendConfig(project_name="x", name="api", server_port=5000)
        config = ProjectConfig(project_name="X", backends=[bc])
        config.validate()
        compose_path = render_compose(config, tmp_path)
        data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
        assert "postgres" in data["services"]

    def test_migrate_service_still_renders(self, tmp_path):
        bc = BackendConfig(project_name="x", name="api", server_port=5000)
        config = ProjectConfig(project_name="X", backends=[bc])
        config.validate()
        compose_path = render_compose(config, tmp_path)
        data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
        assert "api-migrate" in data["services"]

    def test_backend_has_db_env_var(self, tmp_path):
        bc = BackendConfig(project_name="x", name="api", server_port=5000)
        config = ProjectConfig(project_name="X", backends=[bc])
        config.validate()
        compose_path = render_compose(config, tmp_path)
        data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
        env = data["services"]["api"]["environment"]
        assert "APP__DB__URL" in env
