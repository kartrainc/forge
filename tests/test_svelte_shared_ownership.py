"""Svelte's shared runtime has the same integrity boundary as Vue's."""

import pytest

from forge.config import BackendConfig, FrontendConfig, FrontendFramework, ProjectConfig
from forge.generator import generate
from forge.quality.architecture import verify_architecture
from forge.quality.model import digest
from forge.sync.manifest import read_forge_toml


@pytest.mark.parametrize("source", ["lib/paths.ts", "ui/EmptyState.svelte"])
def test_svelte_shared_override_rejected_even_with_restamped_hash(tmp_path, source):
    config = ProjectConfig(
        project_name="svelte-integrity",
        output_dir=str(tmp_path),
        backends=[BackendConfig(name="api", features=["items"])],
        frontend=FrontendConfig(
            framework=FrontendFramework.SVELTE, project_name="web", include_openapi=False
        ),
        include_keycloak=False,
    )
    root = generate(config, quiet=True, dry_run=True)
    app = root / "apps" / config.frontend_slug
    # Service-specific SDKs stay inside the existing custom extension boundary.
    custom = app / "src/custom/api/types.gen.ts"
    custom.parent.mkdir(parents=True)
    custom.write_text("export type BusinessId = string;\n", encoding="utf-8")
    assert verify_architecture(root)["passed"]

    protected = app / "src/lib/shared" / source
    rel = protected.relative_to(root).as_posix()
    manifest = root / "forge.toml"
    record = read_forge_toml(manifest).provenance[rel]
    assert record["ownership"] == "generated"
    protected.write_text("// application override\n", encoding="utf-8")
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(record["sha256"], digest(protected)),
        encoding="utf-8",
    )
    report = verify_architecture(root)
    assert not report["passed"]
    assert any(rel in item and "differs from regeneration" in item for item in report["violations"])
