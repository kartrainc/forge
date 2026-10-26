"""Transactional updates for projects with an explicit ownership recipe."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import tomlkit

from forge.config import BackendLanguage
from forge.errors import ForgeError
from forge.quality.model import RECIPE, digest, ownership, project_path, read_recipe
from forge.sync.manifest import read_forge_toml
from forge.sync.provenance import ProvenanceCollector
from forge.toolchains._runner import run_backend_cmd
from forge.toolchains.go import TIDY


def update_owned_project(root: Path, *, dry_run: bool = False) -> dict:
    """Upgrade protected files; preserve all existing editable scaffold files.

    Old hashes detect edits before mutation; fresh generation supplies new
    bytes. An exception during application rolls every touched file back.
    Changes to editable dependency/configuration files become reviewable
    .forge-merge sidecars, never silently replace local work.
    """
    from forge.generator import generate

    old = read_forge_toml(root / "forge.toml")
    for rel, record in old.provenance.items():
        if (
            record.get("ownership") != "generated"
            and ownership(rel, record["origin"], record.get("template_name")) != "generated"
        ):
            continue
        path = project_path(root, rel)
        if path.is_file() and digest(path) != record["sha256"]:
            raise ValueError(
                f"Protected file edited or missing: {rel}. Restore it before updating."
            )
    config = read_recipe(root, check_generator=False)
    config.options = dict(old.options)
    config.option_origins = dict(old.option_origins)
    go_modules = [f"services/{b.name}" for b in config.backends if b.language == BackendLanguage.GO]
    if go_modules and shutil.which("go") is None:
        raise ValueError(
            f"Updating Go services ({', '.join(go_modules)}) resolves go.mod and go.sum "
            "as generation does: install the go toolchain and run the update again."
        )
    candidate = generate(config, quiet=True, dry_run=True)
    try:
        new = read_forge_toml(candidate / "forge.toml")
        untidy = resolve_go_modules(candidate, go_modules, new.provenance)
        locks = {f"{module}/go.sum": f"{module}/go.mod" for module in go_modules}
        # Regeneration writes the primary app to the generator's fixed
        # apps/<name>. A different recorded location (an edited manifest, a moved
        # app, or a recipe that renamed or replaced the primary) would recreate
        # the app there and delete its protected files: refuse instead. Further
        # apps are identified by their names, so the recipe adds, removes and
        # renames them; a moved one fails the protected-file check above.
        _refuse_moved_primary(old.frontend.app_dir, new.frontend.app_dir)
        writes: dict[str, bytes | None] = {}
        conflicts: list[str] = []
        for rel, record in new.provenance.items():
            path = project_path(root, rel)
            previous = old.provenance.get(rel)
            protected = ownership(rel, record["origin"], record.get("template_name")) == "generated"
            if previous and previous.get("origin") == "user":
                if protected:
                    raise ValueError(
                        f"User-owned file collides with protected output: {rel}. Move its changes into an extension first."
                    )
                new.provenance[rel] = previous
                continue
            if rel in locks:
                continue
            if previous is None and path.exists():
                raise ValueError(f"New generator output collides with user file: {rel}")
            if (
                protected
                and previous
                and previous.get("ownership") != "generated"
                and path.is_file()
                and digest(path) != previous["sha256"]
            ):
                raise ValueError(f"New protected output collides with customized scaffold: {rel}")
            if protected or previous is None or not path.exists():
                writes[rel] = (candidate / rel).read_bytes()
            elif path.is_file() and (
                digest(path) == previous["sha256"] or digest(path) == digest(candidate / rel)
            ):
                # Untouched scaffold can receive a compatible upstream update.
                writes[rel] = (candidate / rel).read_bytes()
            elif (
                path.is_file()
                and digest(candidate / rel) != previous["sha256"]
                # A baseline recorded before tidy is the same upstream go.mod.
                and untidy.get(rel) != previous["sha256"]
            ):
                sidecar = rel + ".forge-merge"
                if (root / sidecar).exists():
                    raise ValueError(f"Resolve existing update conflict first: {sidecar}")
                writes[sidecar] = (candidate / rel).read_bytes()
                conflicts.append(rel)
        for rel, module in locks.items():
            previous = old.provenance.get(rel)
            if rel not in new.provenance or (previous and previous.get("origin") == "user"):
                continue
            # go.sum is resolved from its go.mod, so it follows that file's decision.
            if writes.get(module) is not None or not project_path(root, rel).exists():
                writes[rel] = (candidate / rel).read_bytes()
        for rel, record in old.provenance.items():
            if record.get("ownership") == "generated" and rel not in new.provenance:
                writes[rel] = None
        if not conflicts:
            for rel, record in old.provenance.items():
                if record.get("origin") == "user" and rel not in new.provenance:
                    new.provenance[rel] = record
            manifest = tomlkit.parse((candidate / "forge.toml").read_text(encoding="utf-8"))
            manifest["forge"]["provenance"] = dict(sorted(new.provenance.items()))
            writes["forge.toml"] = tomlkit.dumps(manifest).encode()
            writes[RECIPE] = (candidate / RECIPE).read_bytes()
        else:
            # Do not advance baselines when application is incomplete.
            writes = {k: v for k, v in writes.items() if k.endswith(".forge-merge")}
        report = {
            "passed": not conflicts,
            "backends": [b.name for b in config.backends],
            "fragments_applied": sorted(
                {name for r in new.provenance.values() if (name := r.get("fragment_name"))}
            ),
            "forge_version_before": old.version,
            "forge_version_after": new.version,
            "update_mode": "merge",
            "file_conflicts": len(conflicts),
            "conflicts": conflicts,
            "changes": [
                {"path": rel, "action": "delete" if body is None else "write"}
                for rel, body in sorted(writes.items())
            ],
        }
        if not dry_run:
            modes = {
                rel: (candidate / rel).stat().st_mode & 0o777
                for rel, body in writes.items()
                if body is not None and (candidate / rel).is_file()
            }
            apply_transaction(root, writes, modes=modes)
        return report
    finally:
        shutil.rmtree(candidate)


def resolve_go_modules(
    candidate: Path, modules: list[str], provenance: dict[str, dict]
) -> dict[str, str]:
    """Tidy candidate Go modules and re-record them as generation does.

    Returns each go.mod's pre-tidy digest.
    """
    untidy: dict[str, str] = {}
    collector = ProvenanceCollector(candidate)
    for module in modules:
        manifest = f"{module}/go.mod"
        untidy[manifest] = digest(candidate / manifest)
        try:
            run_backend_cmd(candidate / module, TIDY, "Resolve go.sum", quiet=True, required=True)
        except ForgeError as exc:
            raise ValueError(f"Cannot resolve the Go modules of {module}: {exc}") from exc
        provenance[manifest]["sha256"] = digest(candidate / manifest)
        collector.record(candidate / module / "go.sum", origin="base-template")
    provenance.update({rel: dict(sorted(e.items())) for rel, e in collector.as_dict().items()})
    return untidy


def resolve_owned_conflict(root: Path, relative: str, *, resolution: str) -> dict:
    """Accept the proposed upstream baseline while preserving an explicit choice."""
    if resolution not in {"keep", "replace"}:
        raise ValueError(
            "Resolution must be keep (retain current/merged file) or replace (upstream)"
        )
    path = project_path(root, relative)
    sidecar = project_path(root, relative + ".forge-merge")
    manifest = tomlkit.parse((root / "forge.toml").read_text(encoding="utf-8"))
    record = manifest["forge"]["provenance"][relative]
    if record.get("ownership") == "generated":
        raise ValueError("Protected runtime cannot be resolved to custom code")
    if not sidecar.is_file() or not path.is_file():
        raise ValueError("Both the current file and its update proposal must exist")
    record["sha256"] = digest(sidecar)
    writes = {"forge.toml": tomlkit.dumps(manifest).encode(), relative + ".forge-merge": None}
    if resolution == "replace":
        writes[relative] = sidecar.read_bytes()
    apply_transaction(root, writes)
    return {"passed": True, "resolved": relative, "resolution": resolution}


def apply_transaction(
    root: Path, writes: dict[str, bytes | None], *, modes: dict[str, int] | None = None
) -> None:
    """Preflight every path, then restore original bytes on failed application."""
    paths = {rel: project_path(root, rel) for rel in writes}
    with tempfile.TemporaryDirectory(prefix="forge-update-backup-") as backup:
        originals: dict[str, Path | None] = {}
        for i, (rel, path) in enumerate(paths.items()):
            if path.exists() and not path.is_file():
                raise ValueError(f"Output is not a regular file: {rel}")
            saved = Path(backup) / str(i) if path.exists() else None
            if saved:
                shutil.copy2(path, saved)
            originals[rel] = saved
        touched: list[str] = []
        try:
            for rel, body in writes.items():
                path = paths[rel]
                touched.append(rel)
                if body is None:
                    path.unlink(missing_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(body)
                    if modes and rel in modes:
                        path.chmod(modes[rel])
        except BaseException:
            for rel in reversed(touched):
                saved = originals[rel]
                if saved:
                    shutil.copy2(saved, paths[rel])
                else:
                    paths[rel].unlink(missing_ok=True)
            raise


def migration_report(root: Path) -> dict:
    """Read-only ownership proposal for legacy projects; never adopt modified files."""
    data = read_forge_toml(root / "forge.toml")
    files = []
    for rel, record in data.provenance.items():
        path = project_path(root, rel)
        unchanged = path.is_file() and digest(path) == record["sha256"]
        files.append(
            {
                "path": rel,
                "ownership": ownership(rel, record["origin"], record.get("template_name"))
                if unchanged
                else "user",
                "needs_review": not unchanged,
            }
        )
    return {
        "schema_version": 1,
        "files": files,
        "instructions": "Regenerate using the original configuration into a separate directory. Move custom changes into extension files, then adopt the reviewed recipe and manifest.",
    }


def _refuse_moved_primary(recorded_app: str, regenerated_app: str) -> None:
    """Refuse an update that would move the primary app (see update_owned_project)."""
    if recorded_app and regenerated_app and recorded_app != regenerated_app:
        raise ValueError(
            f"forge.toml records the frontend app at {recorded_app}, but the "
            f"generator writes it to {regenerated_app}. This update does not move "
            f"an app: restore it at {regenerated_app}, or keep the recipe's first "
            f"frontend at {recorded_app}, before updating."
        )
