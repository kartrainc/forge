"""Go backend plumbing: config, manifests, codegen and quality adapters."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from forge.appliers.deps import _add_dependencies, _parse_go_dep
from forge.codegen.enums import EnumSpec, EnumValue, emit_go
from forge.config import BackendConfig, BackendLanguage
from forge.domain.emitters import emit_go_struct
from forge.domain.spec import EntityField, EntitySpec, FieldType
from forge.errors import FragmentError
from forge.extractors.deps import _manifest_path, _parse_fragment_deps, _read_project_deps
from forge.sync.project_to_forge.apply_bundle._deps import _lang_from_manifest_relpath
from forge.toolchains import go as go_toolchain

GO_MOD = """module orders

go 1.27.0

require (
\tgithub.com/google/uuid v1.6.0
\tgolang.org/x/text v0.43.0 // indirect
)

require github.com/jackc/pgx/v5 v5.11.0

replace forge.local/platform-auth-go => ../../packages/platform-auth-go

replace (
\tforge.local/other => ../other // local fork
\texample.com/remote => example.com/fork v1.0.0
)
"""


@pytest.fixture
def go_service(tmp_path: Path) -> tuple[Path, Path]:
    service = tmp_path / "services" / "orders"
    (service / "internal" / "app").mkdir(parents=True)
    (service / "go.mod").write_text(GO_MOD, encoding="utf-8")
    (service / "internal" / "app" / "app.go").write_text(
        "package app\n\n// New builds.\nfunc New() int {\n\tx := 1\n\n\treturn x\n}\n",
        encoding="utf-8",
    )
    return tmp_path, service


def test_go_is_a_builtin_backend_with_a_version_field() -> None:
    from forge.config import BACKEND_REGISTRY

    spec = BACKEND_REGISTRY[BackendLanguage.GO]
    assert spec.template_dir == "services/go-service-template"
    assert spec.version_field == "go_version"
    assert BackendConfig(language=BackendLanguage.GO).go_version == spec.version_choices[0]


def test_go_service_names_cannot_shadow_the_standard_library() -> None:
    with pytest.raises(ValueError, match="standard-library"):
        BackendConfig(name="log", language=BackendLanguage.GO).validate()
    BackendConfig(name="log", language=BackendLanguage.RUST).validate()
    BackendConfig(name="orders", language=BackendLanguage.GO).validate()


def test_go_toolchain(tmp_path, monkeypatch) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(
        go_toolchain,
        "run_backend_cmd",
        lambda directory, cmd, description, quiet=False: commands.append(cmd),
    )
    toolchain = go_toolchain.GO_TOOLCHAIN
    toolchain.install(tmp_path, quiet=True)
    toolchain.post_generate(tmp_path, quiet=True)
    monkeypatch.setattr(go_toolchain.shutil, "which", lambda name: None)
    checks = toolchain.verify(tmp_path, quiet=True)
    assert commands[:2] == [["go", "mod", "tidy"], ["gofmt", "-w", "."]]
    assert ["go", "vet", "./..."] in commands
    assert checks[1].status == "skip"

    monkeypatch.setattr(go_toolchain.shutil, "which", lambda name: "/usr/bin/gofmt")
    for stdout, status in (("", "ok"), ("internal/app/app.go\n", "fail")):
        monkeypatch.setattr(
            go_toolchain.subprocess,
            "run",
            lambda *a, stdout=stdout, **k: subprocess.CompletedProcess(a, 0, stdout, ""),
        )
        assert go_toolchain._gofmt_check(tmp_path, quiet=False).status == status


def test_go_mod_dependency_round_trip(tmp_path) -> None:
    service = tmp_path / "svc"
    service.mkdir()
    go_mod = service / "go.mod"
    go_mod.write_text(GO_MOD, encoding="utf-8")
    _add_dependencies(
        BackendLanguage.GO,
        service,
        ("github.com/google/uuid@v1.7.0", "github.com/redis/go-redis/v9@v9.23.0"),
    )
    text = go_mod.read_text(encoding="utf-8")
    assert "github.com/google/uuid v1.6.0" in text and "v1.7.0" not in text
    assert text.endswith("require (\n\tgithub.com/redis/go-redis/v9 v9.23.0\n)\n")
    _add_dependencies(BackendLanguage.GO, service, ("github.com/redis/go-redis/v9@v9.23.0",))
    assert go_mod.read_text(encoding="utf-8") == text

    manifest = _manifest_path(service, BackendLanguage.GO)
    assert manifest == go_mod
    assert _read_project_deps(go_mod, BackendLanguage.GO) == {
        "github.com/google/uuid": "v1.6.0",
        "github.com/jackc/pgx/v5": "v5.11.0",
        "github.com/redis/go-redis/v9": "v9.23.0",
    }
    assert _parse_fragment_deps(("a.example/m@v1.0.0",), BackendLanguage.GO) == {
        "a.example/m": "v1.0.0"
    }
    assert _lang_from_manifest_relpath("services/svc/go.mod") is BackendLanguage.GO

    with pytest.raises(FragmentError):
        _parse_go_dep("github.com/google/uuid")
    with pytest.raises(FragmentError):
        _add_dependencies(BackendLanguage.GO, tmp_path / "missing", ("a.example/m@v1.0.0",))


def test_go_enum_and_entity_emitters() -> None:
    enum = emit_go(
        EnumSpec(
            name="OrderStatus",
            description="",
            values=(EnumValue(value="DRAFT"), EnumValue(value="in-progress")),
        )
    )
    assert "package enums\n\ntype OrderStatus string" in enum
    assert '\tOrderStatusDraft      OrderStatus = "DRAFT"\n' in enum
    assert "func " not in enum

    def entity(*fields: EntityField) -> str:
        spec = EntitySpec(name="Order", plural="orders", description="", fields=fields)
        return emit_go_struct(spec, module="orders", known_enums={"OrderStatus"})

    plain = entity(EntityField("name", FieldType.STRING), EntityField("total", FieldType.NUMBER))
    assert "import" not in plain
    assert '\tName  string  `json:"name"`\n\tTotal float64 `json:"total"`\n' in plain
    full = entity(
        EntityField("id", FieldType.UUID),
        EntityField("status", FieldType.ENUM, enum="OrderStatus"),
        EntityField("meta", FieldType.JSON, optional=True),
        EntityField("seen", FieldType.ARRAY, of="datetime"),
        EntityField("api_url", FieldType.STRING, optional=True),
        EntityField("count", FieldType.INTEGER, optional=True),
        EntityField("ok", FieldType.BOOLEAN),
        EntityField("day", FieldType.DATE),
        EntityField("owner", FieldType.RELATION, target="User"),
        EntityField("extra", FieldType.ARRAY, of="object"),
        EntityField("kind", FieldType.ENUM),
    )
    assert (
        'import (\n\t"encoding/json"\n\t"time"\n\n'
        '\t"github.com/google/uuid"\n\t"orders/internal/domain/enums"\n)' in full
    )
    for fragment in ("APIURL *string", "Seen   []time.Time", "Extra  []any", "Kind   string"):
        assert fragment in full


def test_go_entity_codegen_writes_the_domain_package(tmp_path) -> None:
    from forge.codegen.pipeline import _emit_go_entity

    spec = EntitySpec(
        name="LineItem",
        plural="line_items",
        description="",
        fields=(EntityField("sku", FieldType.STRING),),
    )
    _emit_go_entity(spec, "orders", tmp_path, None, known_enums=set())
    written = tmp_path / "services/orders/internal/domain/line_item.go"
    text = written.read_text(encoding="utf-8")
    assert text.startswith("// FORGE:BEGIN domain_line_item_struct\n")
    assert "package domain" in text


def test_updater_infers_go_services_from_go_mod(tmp_path) -> None:
    from forge.sync.forge_to_project.updater import _infer_backends

    service = tmp_path / "services" / "orders"
    service.mkdir(parents=True)
    (service / "go.mod").write_text("module orders\n", encoding="utf-8")
    assert [b.language for b in _infer_backends(tmp_path)] == [BackendLanguage.GO]


def test_cli_config_threads_go_version() -> None:
    from forge.cli.builder import _build_config
    from forge.cli.parser import _build_parser

    args = _build_parser().parse_args(["--yes"])
    cfg = {
        "project_name": "shop",
        "backends": [{"name": "orders", "language": "go", "go_version": "1.26"}],
        "frontend": {"framework": "none"},
    }
    config = _build_config(args, cfg)
    assert config.backends[0].language is BackendLanguage.GO
    assert config.backends[0].go_version == "1.26"
    single = _build_config(
        _build_parser().parse_args(["--backend-language", "go", "--go-version", "1.26"]),
        {"project_name": "shop", "frontend": {"framework": "none"}},
    )
    assert single.backends[0].go_version == "1.26"
