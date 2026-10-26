"""Unit tests for the backend application-template registry + config wiring.

The backend analogue of ``tests/test_layout_variants.py``: covers the
``BackendApplicationTemplate`` registry (:mod:`forge.backend_app_templates`),
``BackendConfig.app_template`` validation, the ``add_backend_application_template``
plugin API, and a dry-run generation of the additional ``worker`` variant.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forge import backend_app_templates as bat
from forge.config import BackendConfig, BackendLanguage, ProjectConfig

# The default-on fragments whose injections target the crud-service file
# shape (``src/app/main.py`` etc.) declare ``excluded_app_templates=
# ("worker",)``, so a stock worker config generates without any opt-out
# dance. This tuple documents that contract and drives the exclusion test.
_WORKER_EXCLUDED_FRAGMENTS = (
    "correlation_id",
    "error_port",
    "pii_redaction",
    "rate_limit",
    "reliability_connection_pool",
    "security_headers",
)


@pytest.fixture(autouse=True)
def _isolate_registry():
    """Restore built-in variants after any test that mutates the registry."""
    yield
    bat._reset_for_tests()


# --- registry ---------------------------------------------------------------


def test_builtin_crud_service_registered_for_every_language():
    """Each built-in language ships ``crud-service`` pointing at its baseline
    template, self-contained (base == "") — the byte-identical default."""
    from forge.config import BACKEND_REGISTRY

    for lang in (BackendLanguage.PYTHON, BackendLanguage.NODE, BackendLanguage.RUST):
        t = bat.get_backend_application_template(lang, "crud-service")
        assert t is not None, f"crud-service missing for {lang.value}"
        assert t.template_dir == BACKEND_REGISTRY[lang].template_dir
        assert t.base_template_dir == ""  # self-contained single render (baseline)


def test_default_constant_is_crud_service():
    assert bat.DEFAULT_BACKEND_TEMPLATE == "crud-service"
    assert BackendConfig(name="b", language=BackendLanguage.PYTHON).app_template == "crud-service"


def test_worker_variant_registered_for_python_only():
    t = bat.get_backend_application_template(BackendLanguage.PYTHON, "worker")
    assert t is not None
    assert t.template_dir == "services/python/worker"
    assert t.base_template_dir == ""
    # Worker is a Python-only variant — not registered for node/rust.
    assert bat.get_backend_application_template(BackendLanguage.NODE, "worker") is None
    assert bat.get_backend_application_template(BackendLanguage.RUST, "worker") is None


def test_available_backend_templates_per_language():
    assert bat.available_backend_templates(BackendLanguage.PYTHON) == (
        "crud-service",
        "tenant-management-service",
        "worker",
    )
    assert bat.available_backend_templates(BackendLanguage.NODE) == ("crud-service",)
    assert bat.available_backend_templates(BackendLanguage.RUST) == ("crud-service",)


def test_all_backend_template_names():
    assert bat.all_backend_template_names() == (
        "crud-service",
        "tenant-management-service",
        "worker",
    )


def test_get_unknown_variant_returns_none():
    assert bat.get_backend_application_template(BackendLanguage.PYTHON, "nope") is None


def test_register_and_lookup_roundtrip():
    bat.register_backend_application_template(
        bat.BackendApplicationTemplate(
            BackendLanguage.NODE, "demovariant1", "services/node/demovariant1", "Demo"
        )
    )
    got = bat.get_backend_application_template(BackendLanguage.NODE, "demovariant1")
    assert got is not None and got.display_label == "Demo"
    assert "demovariant1" in bat.available_backend_templates(BackendLanguage.NODE)


def test_duplicate_registration_raises():
    with pytest.raises(ValueError, match="already registered"):
        bat.register_backend_application_template(
            bat.BackendApplicationTemplate(
                BackendLanguage.PYTHON, "crud-service", "services/python-service-template", "dup"
            )
        )


def test_reset_for_tests_restores_builtins():
    bat.register_backend_application_template(
        bat.BackendApplicationTemplate(
            BackendLanguage.RUST, "demovariant2", "services/rust/demovariant2", "Demo"
        )
    )
    assert bat.get_backend_application_template(BackendLanguage.RUST, "demovariant2") is not None
    bat._reset_for_tests()
    # The transient variant is gone; built-ins are back.
    assert bat.get_backend_application_template(BackendLanguage.RUST, "demovariant2") is None
    assert bat.get_backend_application_template(BackendLanguage.PYTHON, "crud-service") is not None
    assert bat.get_backend_application_template(BackendLanguage.PYTHON, "worker") is not None


def test_unsupported_variant_hidden_from_available_but_resolvable():
    bat.register_backend_application_template(
        bat.BackendApplicationTemplate(
            BackendLanguage.NODE, "demovariant3", "services/node/demovariant3", "X", supported=False
        )
    )
    assert "demovariant3" not in bat.available_backend_templates(BackendLanguage.NODE)
    assert bat.get_backend_application_template(BackendLanguage.NODE, "demovariant3") is not None


# --- BackendConfig.validate() ----------------------------------------------


def test_validate_accepts_crud_service():
    BackendConfig(name="b", language=BackendLanguage.PYTHON, app_template="crud-service").validate()


def test_validate_accepts_worker_variant():
    BackendConfig(
        name="nw", language=BackendLanguage.PYTHON, app_template="worker", features=[]
    ).validate()


def test_validate_rejects_unknown_variant():
    cfg = BackendConfig(name="b", language=BackendLanguage.PYTHON, app_template="ghost")
    with pytest.raises(ValueError, match="[Aa]pplication template 'ghost' is not available"):
        cfg.validate()


def test_validate_variant_is_language_scoped():
    # ``worker`` is Python-only — it must not validate for node.
    BackendConfig(name="nw", language=BackendLanguage.PYTHON, app_template="worker").validate()
    with pytest.raises(ValueError, match="not available for node"):
        BackendConfig(name="nw", language=BackendLanguage.NODE, app_template="worker").validate()


# --- plugin API surface -----------------------------------------------------


def test_api_add_backend_application_template_registers_variant():
    from forge.api import ForgeAPI, PluginRegistration

    api = ForgeAPI(PluginRegistration(name="bat_plugin", module="m"))
    api.add_backend_application_template(
        BackendLanguage.PYTHON,
        "demovariant1",
        "services/python/demovariant1",
        "Demo Variant",
        base_template_dir="services/python-service-template",
    )
    t = bat.get_backend_application_template(BackendLanguage.PYTHON, "demovariant1")
    assert t is not None
    assert t.display_label == "Demo Variant"
    assert t.base_template_dir == "services/python-service-template"
    assert "demovariant1" in bat.available_backend_templates(BackendLanguage.PYTHON)


def test_api_add_backend_application_template_accepts_string_language():
    from forge.api import ForgeAPI, PluginRegistration

    api = ForgeAPI(PluginRegistration(name="bat_plugin", module="m"))
    api.add_backend_application_template(
        "node", "demovariant2", "services/node/demovariant2", "Demo"
    )
    assert bat.get_backend_application_template(BackendLanguage.NODE, "demovariant2") is not None


def test_api_add_backend_application_template_duplicate_raises():
    from forge.api import ForgeAPI, PluginRegistration
    from forge.errors import PluginError

    api = ForgeAPI(PluginRegistration(name="bat_plugin", module="m"))
    with pytest.raises(PluginError, match="already registered"):
        api.add_backend_application_template(
            "python", "crud-service", "services/python-service-template", "dup"
        )


# --- generation -------------------------------------------------------------


def test_worker_variant_renders_distinctive_files(tmp_path: Path):
    """A dry-run generation of the worker variant emits its distinctive
    ``src/worker/`` package and NOT the crud-service ``src/app/main.py``.

    Deliberately uses DEFAULT options: the HTTP-shaped default-on fragments
    declare ``excluded_app_templates=("worker",)`` so a stock worker config
    generates without the previously-required 8-option opt-out dance (the
    pre-fix behavior was an InjectionError on the missing src/app/main.py).
    """
    cfg = ProjectConfig(
        project_name="wk_demo",
        output_dir=str(tmp_path),
        backends=[
            BackendConfig(
                name="notify-worker",
                project_name="wk_demo",
                language=BackendLanguage.PYTHON,
                app_template="worker",
                features=[],
            )
        ],
        frontend=None,
        options={},
    )
    from forge.generator import generate

    root = generate(cfg, quiet=True, dry_run=True)
    svc = root / "services" / "notify-worker"
    # Distinctive worker files rendered with substituted Jinja vars.
    for rel in (
        "src/worker/worker.py",
        "src/worker/config.py",
        "src/worker/observability.py",
        "src/worker/__main__.py",
        "pyproject.toml",
    ):
        assert (svc / rel).is_file(), f"missing worker file: {rel}"
    # The worker shape has NO FastAPI HTTP entrypoint.
    assert not (svc / "src" / "app" / "main.py").exists()
    # Jinja substitution actually happened — the generator threads the
    # backend name through as ``project_slug`` (see variable_mapper).
    worker_src = (svc / "src" / "worker" / "config.py").read_text(encoding="utf-8")
    assert 'service_name: str = "notify-worker"' in worker_src
    # The pyproject names the worker package, not the crud ``app`` package.
    pyproject = (svc / "pyproject.toml").read_text(encoding="utf-8")
    assert 'packages = ["src/worker"]' in pyproject
    assert "fastapi" not in pyproject


def test_http_shaped_default_fragments_exclude_worker():
    """Every default-on fragment that injects into the crud-service HTTP
    shape must opt out of the worker variant, or stock worker generation
    crashes with InjectionError on the missing ``src/app/main.py``."""
    from forge.fragments import FRAGMENT_REGISTRY

    for name in _WORKER_EXCLUDED_FRAGMENTS:
        frag = FRAGMENT_REGISTRY.get(name)
        assert frag is not None, name
        assert "worker" in frag.excluded_app_templates, (
            f"fragment '{name}' targets the crud-service HTTP shape and must "
            f"declare excluded_app_templates=('worker', ...)"
        )


def test_user_selected_excluded_option_rejected_on_worker_only_project():
    """A USER-origin option whose every fragment is excluded on every backend
    variant must hard-error at resolve time (silently generating nothing
    would betray an explicit selection); the same value as a persisted
    DEFAULT must keep auto-skipping."""
    from forge.errors import OptionsError

    def _cfg(origins):
        return ProjectConfig(
            project_name="wk_guard",
            backends=[
                BackendConfig(
                    name="notify-worker",
                    project_name="wk_guard",
                    language=BackendLanguage.PYTHON,
                    app_template="worker",
                    features=[],
                )
            ],
            frontend=None,
            options={"middleware.rate_limit": True},
            option_origins=origins,
        )

    from forge.capability_resolver import resolve

    with pytest.raises(OptionsError, match="app_template"):
        resolve(_cfg({"middleware.rate_limit": "user"}))
    # Same option as a persisted default: no error, fragment auto-skips.
    resolve(_cfg({"middleware.rate_limit": "default"}))


def test_user_selected_excluded_option_allowed_with_compatible_backend():
    """The guard is per-project, not per-backend: when ANY backend can host
    the fragment, an explicit selection is satisfied and must not error."""
    cfg = ProjectConfig(
        project_name="wk_mixed",
        backends=[
            BackendConfig(
                name="api",
                project_name="wk_mixed",
                language=BackendLanguage.PYTHON,
                app_template="crud-service",
                features=[],
            ),
            BackendConfig(
                name="notify-worker",
                project_name="wk_mixed",
                language=BackendLanguage.PYTHON,
                app_template="worker",
                features=[],
            ),
        ],
        frontend=None,
        options={"middleware.rate_limit": True},
        option_origins={"middleware.rate_limit": "user"},
    )
    from forge.capability_resolver import resolve

    plan = resolve(cfg)
    assert any(rf.fragment.name == "rate_limit" for rf in plan.ordered)


def test_crud_service_renders_baseline_shape(tmp_path: Path):
    """The default crud-service variant renders the baseline FastAPI shape —
    proving the dispatch path is exercised and unchanged for the default."""
    cfg = ProjectConfig(
        project_name="crud_demo",
        output_dir=str(tmp_path),
        backends=[
            BackendConfig(
                name="api",
                project_name="crud_demo",
                language=BackendLanguage.PYTHON,
                app_template="crud-service",
                features=["items"],
            )
        ],
        frontend=None,
    )
    from forge.generator import generate

    root = generate(cfg, quiet=True, dry_run=True)
    svc = root / "services" / "api"
    # The crud-service emits the FastAPI app + the example items entity.
    assert (svc / "src" / "app" / "main.py").is_file()
    assert (svc / "src" / "app" / "api" / "v1" / "endpoints" / "items.py").is_file()
    # ...and NOT the worker package.
    assert not (svc / "src" / "worker").exists()


def test_two_stage_variant_renders_nonempty_app_title(tmp_path: Path):
    """A two-stage variant overlay (tenant-management-service) must render a
    non-empty ``app.title`` in config/default.yaml.

    Regression for the chronic multitenant-saas nightly boot crash: the
    overlay's copier.yml declares no questions, so it relied on the generator
    supplying every ctx var — but ``project_title`` was a copier ``when:false``
    default the overlay never computed, so ``title: ""`` rendered and the
    generated FastAPI app aborted at boot ("A title must be provided for
    OpenAPI"). ``variable_mapper.backend_context`` now supplies project_title.
    """
    cfg = ProjectConfig(
        project_name="tms_demo",
        output_dir=str(tmp_path),
        backends=[
            BackendConfig(
                name="tms",
                project_name="tms_demo",
                language=BackendLanguage.PYTHON,
                app_template="tenant-management-service",
                features=["items"],
            )
        ],
        frontend=None,
    )
    from forge.generator import generate

    root = generate(cfg, quiet=True, dry_run=True)
    cfg_yaml = (root / "services" / "tms" / "config" / "default.yaml").read_text(encoding="utf-8")
    # The overlay overwrites the base config; its title must not be empty.
    assert 'title: ""' not in cfg_yaml, "two-stage variant rendered an empty app.title"
    assert 'title: "Tms"' in cfg_yaml


@pytest.mark.parametrize("variant", ["api-gateway", "service-proxy"])
def test_removed_forwarding_templates_rejected(variant):
    assert bat.get_backend_application_template(BackendLanguage.PYTHON, variant) is None
    with pytest.raises(ValueError, match="not available"):
        BackendConfig(name="old", language=BackendLanguage.PYTHON, app_template=variant).validate()
