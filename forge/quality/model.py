"""Portable generation recipe and source ownership, independent of origin."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from forge import __version__
from forge.config import BackendConfig, FrontendConfig, ProjectConfig
from forge.config._backend import resolve_backend_language
from forge.config._frontend import resolve_frontend_framework

RECIPE = ".forge/quality.json"
Ownership = Literal["generated", "scaffold", "user"]
SOURCE_SUFFIXES = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".rs",
    ".go",
    ".dart",
    ".vue",
    ".svelte",
}
IGNORED = {
    ".git",
    ".venv",
    "node_modules",
    "target",
    "vendor",
    "testdata",
    "dist",
    "build",
    "__pycache__",
    ".dart_tool",
    ".pytest_cache",
    ".ruff_cache",
    "htmlcov",
    "coverage",
    ".svelte-kit",
    ".next",
    "playwright-report",
    "test-results",
}


def digest(path: Path) -> str:
    """Hash logical text consistently across Windows and Linux."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def project_path(root: Path, relative: str) -> Path:
    """Refuse escaping or symlinked paths before reading or writing a project."""
    rel = PurePosixPath(relative)
    if rel.is_absolute() or ".." in rel.parts or "\\" in relative or not rel.parts:
        raise ValueError(f"Invalid project path: {relative!r}")
    path = root.joinpath(*rel.parts)
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Path escapes project: {relative}")
    if any(root.joinpath(*rel.parts[:i]).is_symlink() for i in range(1, len(rel.parts) + 1)):
        raise ValueError(f"Symlink is not an ownership boundary: {relative}")
    return path


def ownership(
    path: str, origin: str = "base-template", template_name: str | None = None
) -> Ownership:
    """Default policy: reusable runtime/contracts are owned, business seeds are editable.

    Fragment runtime files are reusable implementations. Tests, docs, migrations,
    configuration and application composition remain editable scaffolds. Policy
    is evaluated from trusted generator output, never the project's own hashes.
    """
    p = PurePosixPath(path)
    if origin == "user":
        return "user"
    if (
        any(
            part in {"tests", "test", "docs", "migrations", "alembic", "custom"} for part in p.parts
        )
        or any(marker in p.name for marker in (".test.", ".spec."))
        or p.name.endswith("_test.go")
    ):
        return "scaffold"
    if p.suffix not in SOURCE_SUFFIXES:
        return "scaffold"
    if template_name in {
        "_codegen",
        "_domain_emitter",
        "_contract_types",
        "_transform_adapters",
        "_capabilities",
    }:
        return "generated"
    if p.parts[0] == "apps" and (
        (len(p.parts) >= 5 and p.parts[2:4] == ("src", "shared"))
        or (len(p.parts) >= 6 and p.parts[2:5] == ("src", "lib", "shared"))
    ):
        return "generated"
    if len(p.parts) >= 5 and p.parts[0] == "services" and p.parts[2:4] == ("src", "lib"):
        return "generated"
    if any(
        part
        in {
            "sdks",
            "packages",
            "ports",
            "core",
            "middleware",
            "_template",
            "generated",
            "forge_core",
            "platform_auth",
        }
        for part in p.parts
    ):
        return "generated"
    if p.stem in {"forge_core", "platform_auth"}:
        return "generated"
    if ".gen." in p.name or p.stem in {"ui_protocol", "ui-protocol"}:
        return "generated"
    return "generated" if origin == "fragment" else "scaffold"


def source_fingerprint() -> str:
    """Fingerprint the installed generator, including templates and gate code."""
    root = Path(__file__).resolve().parents[1]
    result = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix()):
        rel = path.relative_to(root)
        if (
            not path.is_file()
            or rel.parts[0] == "plans"  # Development notes are not installed runtime inputs.
            or set(rel.parts) & IGNORED
            or path.suffix in {".pyc", ".pyo"}
            or path.name == ".coverage"
            or path.name.startswith(".coverage.")
        ):
            continue
        result.update(rel.as_posix().encode())
        result.update(bytes.fromhex(digest(path)))
    return result.hexdigest()


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum) or hasattr(value, "value"):
        return value.value
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Nonportable generation input: {type(value).__name__}")


def write_recipe(root: Path, config: ProjectConfig) -> None:
    raw = asdict(config)
    raw["output_dir"] = "."
    if config.frontend and config.frontend.framework.value == "none":
        raw["frontend"] = None
    payload = {
        "schema_version": 1,
        "generator_version": __version__,
        "generator_requirement": generator_requirement(),
        "generator_sha256": source_fingerprint(),
        "plugin_requirements": plugin_requirements(),
        "config": raw,
    }
    path = root / RECIPE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, default=_json_value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def plugin_requirements() -> list[str]:
    from importlib.metadata import entry_points

    return sorted(
        {
            f"{entry.dist.metadata['Name']}=={entry.dist.version}"
            for entry in entry_points(group="forge.plugins")
            if entry.dist
        }
    )


def generator_requirement() -> str:
    """Use a commit pin for repository installs and an exact release otherwise."""
    checkout = Path(__file__).resolve().parents[2]
    if (checkout / ".git").exists():
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=checkout,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.strip()
        return f"forge-cli @ git+https://github.com/cchifor/forge@{commit}"
    from importlib.metadata import distribution

    direct = distribution("forge-cli").read_text("direct_url.json")
    if direct:
        data = json.loads(direct)
        commit = data.get("vcs_info", {}).get("commit_id")
        if commit:
            return f"forge-cli @ git+{data['url']}@{commit}"
    return f"forge-cli=={__version__}"


def read_recipe(root: Path, *, check_generator: bool = True) -> ProjectConfig:
    data = json.loads(project_path(root, RECIPE).read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError("Unsupported quality recipe version")
    if check_generator and data.get("generator_sha256") != source_fingerprint():
        raise ValueError(
            "Generator differs from recorded source. Install the pinned generator or run --update."
        )
    if check_generator and data.get("plugin_requirements", []) != plugin_requirements():
        raise ValueError("Installed plugins differ from the recorded recipe")
    raw = dict(data["config"])
    backends = []
    for item in raw.pop("backends"):
        item = dict(item)
        item["language"] = resolve_backend_language(item["language"])
        backends.append(BackendConfig(**item))
    frontend = raw.pop("frontend")
    if frontend is not None:
        frontend["framework"] = resolve_frontend_framework(frontend["framework"])
        frontend = FrontendConfig(**frontend)
    config = ProjectConfig(backends=backends, frontend=frontend, **raw)
    config.validate()
    return config


def subjects(config: ProjectConfig, root: Path | None = None) -> list[dict]:
    items: list[dict] = [
        {
            "name": b.name,
            "path": f"services/{b.name}",
            "language": b.language.value,
            "runtime": b.python_version
            if b.language.value == "python"
            else b.node_version
            if b.language.value == "node"
            else b.go_version
            if b.language.value == "go"
            else "stable",
        }
        for b in config.backends
        if config.backend_mode == "generate"
    ]
    if config.frontend and config.frontend.framework.value != "none":
        items.append(
            {
                "name": config.frontend_slug,
                "path": f"apps/{config.frontend_slug}",
                "language": config.frontend.framework.value,
                "runtime": "stable",
            }
        )
    if root:
        for parent in [
            root / "sdks",
            root / "packages",
            *root.glob("apps/*/packages"),
            *root.glob("services/*/sdks"),
            *root.glob("services/*/packages"),
        ]:
            if not parent.is_dir():
                continue
            for package in sorted(parent.iterdir()):
                if not package.is_dir():
                    continue
                for manifest, language in (
                    ("pyproject.toml", "python"),
                    ("package.json", "node"),
                    ("Cargo.toml", "rust"),
                    ("go.mod", "go"),
                    ("pubspec.yaml", "flutter"),
                ):
                    if (package / manifest).is_file():
                        rel = package.relative_to(root).as_posix()
                        items.append(
                            {
                                "name": rel.replace("/", "-"),
                                "path": rel,
                                "language": language,
                                "shared": True,
                            }
                        )
                        break
    return items
