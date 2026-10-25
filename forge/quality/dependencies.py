"""Explicit dependency resolution and frozen installs for generated workspaces."""

from __future__ import annotations

import os
from pathlib import Path

from forge.quality.model import read_recipe, subjects
from forge.quality.runner import _execute


def dependencies(root: Path, *, lock: bool = False) -> dict:
    """Lock is an intentional mutation; install never silently changes a lock."""
    inventory = subjects(read_recipe(root))
    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    commands: list[tuple[Path, list[str]]] = []
    node_roots: set[Path] = set()
    rust_roots: set[Path] = set()
    for item in inventory:
        service = root / item["path"]
        language = item["language"]
        if language == "python":
            commands.append(
                (service, ["uv", "lock"] if lock else ["uv", "sync", "--locked", "--all-groups"])
            )
        elif language in {"node", "vue", "svelte"}:
            # Only members declared by the workspace use the root lock.
            import fnmatch
            import json

            workspace = (
                json.loads((root / "package.json").read_text(encoding="utf-8")).get(
                    "workspaces", []
                )
                if (root / "package.json").exists()
                else []
            )
            node_roots.add(
                root
                if any(fnmatch.fnmatch(item["path"], pattern) for pattern in workspace)
                else service
            )
        elif language == "rust":
            rust_roots.add(root if (root / "Cargo.toml").is_file() else service)
        elif language == "go":
            commands.extend(
                [(service, ["go", "mod", "tidy"])]
                if lock
                else [(service, ["go", "mod", "download"]), (service, ["go", "mod", "verify"])]
            )
        elif language == "flutter":
            commands.append(
                (service, ["flutter", "pub", "get", *([] if lock else ["--enforce-lockfile"])])
            )
    for directory in sorted(node_roots):
        commands.append(
            (
                directory,
                ["npm", "install", "--package-lock-only", "--ignore-scripts"]
                if lock
                else ["npm", "ci"],
            )
        )
    for directory in sorted(rust_roots):
        commands.append(
            (directory, ["cargo", "generate-lockfile"] if lock else ["cargo", "fetch", "--locked"])
        )
    for directory, command in commands:
        _execute(command, directory, env)
    if not lock:
        for item in inventory:
            service = root / item["path"]
            if item["language"] == "node":
                _execute(["npm", "run", "generate", "--if-present"], service, env)
            elif item["language"] == "svelte":
                _execute(["npx", "--no-install", "svelte-kit", "sync"], service, env)
            if item["language"] in {"vue", "svelte"}:
                _execute(["npx", "--no-install", "playwright", "install", "chromium"], service, env)
            elif item["language"] == "flutter":
                _execute(
                    ["dart", "run", "build_runner", "build", "--delete-conflicting-outputs"],
                    service,
                    env,
                )
    return {
        "passed": True,
        "resolved" if lock else "installed": [str(p.relative_to(root)) for p, _ in commands],
    }
