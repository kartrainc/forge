"""Applier for a fragment's language-specific dependency adds.

Merges fragment-declared deps into the target backend's package
manifest (``pyproject.toml`` / ``package.json`` / ``Cargo.toml`` /
``go.mod``) in a
way that preserves user-authored entries. Raises
:class:`FragmentError` when the manifest is missing, malformed, or the
fragment's spec string doesn't parse.

Epic 1b (P1.1) lifted the bodies into this module; the orchestrating
``_apply_fragment`` entry point that used to call them now lives at
:mod:`forge.sync.forge_to_project.updater` (1.2.0-alpha.1).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import tomlkit

from forge.config import BackendLanguage
from forge.errors import (
    FRAGMENT_DEP_SPEC_INVALID,
    FRAGMENT_DEPS_FILE_MISSING,
    FRAGMENT_DEPS_SECTION_MISSING,
    FragmentError,
)

if TYPE_CHECKING:
    from forge.appliers.plan import FragmentPlan
    from forge.fragment_context import FragmentContext


class FragmentDepsApplier:
    """Adds fragment dependencies to the backend's package manifest."""

    def apply(self, ctx: FragmentContext, plan: FragmentPlan) -> None:
        if not plan.dependencies:
            return
        _add_dependencies(
            ctx.backend_config.language,
            ctx.backend_dir,
            plan.dependencies,
            project_root=ctx.project_root,
        )


def _add_dependencies(
    lang: BackendLanguage,
    backend_dir: Path,
    deps: tuple[str, ...],
    *,
    project_root: Path | None = None,
) -> None:
    """Dispatch ``deps`` into the right per-language manifest editor."""
    if not deps:
        return
    if lang is BackendLanguage.PYTHON:
        _add_python_deps(backend_dir / "pyproject.toml", deps)
    elif lang is BackendLanguage.NODE:
        _add_node_deps(backend_dir / "package.json", deps, project_root=project_root)
    elif lang is BackendLanguage.RUST:
        _add_rust_deps(backend_dir / "Cargo.toml", deps)
    elif lang is BackendLanguage.GO:
        _add_go_deps(backend_dir / "go.mod", deps)


def _add_python_deps(pyproject: Path, deps: tuple[str, ...]) -> None:
    if not pyproject.is_file():
        raise FragmentError(
            f"pyproject.toml not found at {pyproject}",
            code=FRAGMENT_DEPS_FILE_MISSING,
            context={"path": str(pyproject), "language": "python"},
        )
    doc = tomlkit.parse(pyproject.read_text(encoding="utf-8"))
    project = doc.get("project")
    if project is None:
        raise FragmentError(
            f"{pyproject}: [project] section missing",
            code=FRAGMENT_DEPS_SECTION_MISSING,
            context={"path": str(pyproject), "section": "project"},
        )
    existing = list(project.get("dependencies", []))
    existing_names = {_py_dep_name(d): d for d in existing}
    for dep in deps:
        name = _py_dep_name(dep)
        if name in existing_names:
            continue
        existing.append(dep)
        existing_names[name] = dep
    project["dependencies"] = existing
    pyproject.write_text(tomlkit.dumps(doc), encoding="utf-8")


def _py_dep_name(dep: str) -> str:
    """Extract the package name from a PEP 508 spec like `slowapi>=0.1.9`."""
    for sep in ("==", ">=", "<=", "~=", "!=", ">", "<", "["):
        if sep in dep:
            return dep.split(sep, 1)[0].strip().lower()
    return dep.strip().lower()


def _add_node_deps(
    package_json: Path,
    deps: tuple[str, ...],
    *,
    project_root: Path | None = None,
) -> None:
    if not package_json.is_file():
        raise FragmentError(
            f"package.json not found at {package_json}",
            code=FRAGMENT_DEPS_FILE_MISSING,
            context={"path": str(package_json), "language": "node"},
        )
    raw = package_json.read_text(encoding="utf-8")
    data = json.loads(raw)
    deps_obj: dict[str, Any] = data.setdefault("dependencies", {})
    for dep in deps:
        # Node dep spec: "name@version" or "name" for latest.
        if "@" in dep and not dep.startswith("@"):
            name, version = dep.split("@", 1)
        elif dep.startswith("@"):
            # Scoped package like "@fastify/rate-limit@1.2.3"
            head, _, tail = dep[1:].partition("@")
            if tail:
                name, version = "@" + head, tail
            else:
                name, version = dep, "latest"
        else:
            name, version = dep, "latest"
        if version.startswith("workspace:"):
            # npm (≤ v10) doesn't support the `workspace:` URL protocol —
            # only pnpm and yarn berry do. Rewrite to a relative ``file:``
            # path pointing at the conventional in-tree SDK location at
            # ``<project>/packages/<unscoped-name>/``. The path is computed
            # *unconditionally* (no on-disk existence check) because
            # project-scoped SDK fragments are applied AFTER backend-
            # scoped middleware fragments in the generator pipeline —
            # the SDK directory doesn't exist yet when this applier runs,
            # but it will by the time ``npm install`` runs in
            # ``toolchain.install``.
            rewritten = _rewrite_workspace_spec(package_json, name, project_root)
            if rewritten is not None:
                version = rewritten
        if name not in deps_obj:
            deps_obj[name] = version
    package_json.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _rewrite_workspace_spec(
    package_json: Path,
    name: str,
    project_root: Path | None,
) -> str | None:
    """Resolve ``workspace:*`` to ``file:<relative-path>`` against an in-tree SDK.

    Convention: SDKs live at ``<project_root>/packages/<unscoped-name>/``.
    When ``project_root`` is supplied (the normal path from
    :class:`FragmentDepsApplier`), the SDK path is computed
    unconditionally — the SDK fragment is project-scoped and applied
    *after* this backend-scoped middleware fragment, so the directory
    doesn't exist on disk at this moment.

    The fallback path (``project_root=None``, kept for direct callers and
    legacy tests) walks up from the consuming ``package.json`` looking
    for ``<ancestor>/packages/<unscoped-name>/`` on disk. Returns ``None``
    if it can't find it — preserving the original ``workspace:*`` spec
    so ``npm install`` fails loudly with the same EUNSUPPORTEDPROTOCOL
    rather than silently writing a broken ``file:./missing`` path.

    Returns the file-protocol spec with a forward-slash-normalized
    relative path (npm and pnpm both accept POSIX-style separators on
    Windows).
    """
    import os  # noqa: PLC0415

    unscoped = name.split("/", 1)[1] if name.startswith("@") else name
    backend_dir = package_json.parent.resolve()

    if project_root is not None:
        candidate = project_root.resolve() / "packages" / unscoped
        rel = os.path.relpath(candidate, backend_dir)
        return "file:" + rel.replace(os.sep, "/")

    current = backend_dir
    while True:
        candidate = current / "packages" / unscoped
        if candidate.is_dir():
            rel = os.path.relpath(candidate, backend_dir)
            return "file:" + rel.replace(os.sep, "/")
        if current.parent == current:
            return None
        current = current.parent


def _parse_go_dep(dep: str) -> tuple[str, str]:
    """Split ``module@version`` (the only Go form; versions are exact).

    >>> _parse_go_dep("github.com/redis/go-redis/v9@v9.23.0")
    ('github.com/redis/go-redis/v9', 'v9.23.0')
    """
    module, sep, version = dep.strip().rpartition("@")
    if not sep or not module or not version.startswith("v"):
        raise FragmentError(
            f"Go dependency {dep!r} must be 'module@vX.Y.Z'",
            code=FRAGMENT_DEP_SPEC_INVALID,
            context={"dep": dep, "language": "go"},
        )
    return module, version


def _go_required_modules(text: str) -> set[str]:
    """Module paths already named by a ``require`` directive in go.mod."""
    modules: set[str] = set()
    in_block = False
    for raw in text.splitlines():
        line = raw.split("//", 1)[0].strip()
        if in_block:
            if line == ")":
                in_block = False
            elif line:
                modules.add(line.split()[0])
        elif line == "require (":
            in_block = True
        elif line.startswith("require "):
            modules.add(line.split()[1])
    return modules


def _add_go_deps(go_mod: Path, deps: tuple[str, ...]) -> None:
    """Add ``require`` lines for modules go.mod does not name yet.

    Versions are exact (minimal version selection); ``go mod tidy`` resolves
    go.sum and indirect modules afterwards. Existing requirements are
    preserved — forge never clobbers hand-edited versions.
    """
    if not go_mod.is_file():
        raise FragmentError(
            f"go.mod not found at {go_mod}",
            code=FRAGMENT_DEPS_FILE_MISSING,
            context={"path": str(go_mod), "language": "go"},
        )
    text = go_mod.read_text(encoding="utf-8")
    present = _go_required_modules(text)
    additions = [
        f"{module} {version}"
        for module, version in (_parse_go_dep(dep) for dep in deps)
        if module not in present
    ]
    if not additions:
        return
    if not text.endswith("\n"):
        text += "\n"
    text += "\nrequire (\n" + "".join(f"\t{line}\n" for line in additions) + ")\n"
    go_mod.write_text(text, encoding="utf-8")


def _add_rust_deps(cargo_toml: Path, deps: tuple[str, ...]) -> None:
    """Merge Cargo dependencies. Two forms supported:

    - Shorthand: ``"name@version"`` → ``name = "version"``.
    - Full TOML: ``'name = { version = "x", features = [...] }'`` →
      parsed verbatim so features / git / default-features work.

    Existing entries are preserved — forge never clobbers hand-edited deps.
    """
    if not cargo_toml.is_file():
        raise FragmentError(
            f"Cargo.toml not found at {cargo_toml}",
            code=FRAGMENT_DEPS_FILE_MISSING,
            context={"path": str(cargo_toml), "language": "rust"},
        )
    doc = tomlkit.parse(cargo_toml.read_text(encoding="utf-8"))
    table = doc.setdefault("dependencies", tomlkit.table())
    for dep in deps:
        name, value = _parse_rust_dep(dep)
        if name not in table:
            table[name] = value
    cargo_toml.write_text(tomlkit.dumps(doc), encoding="utf-8")


def _parse_rust_dep(dep: str) -> tuple[str, Any]:
    """Parse a Cargo dep spec into ``(name, value)`` where value is either a
    version string or a tomlkit-parsed inline table.

    >>> _parse_rust_dep("tower@0.5")
    ('tower', '0.5')
    >>> _parse_rust_dep('opentelemetry-otlp = { version = "0.27", features = ["grpc-tonic"] }')  # doctest: +ELLIPSIS
    ('opentelemetry-otlp', ...)
    """
    stripped = dep.strip()
    # Full-form check: "name = <toml>" — detected by a top-level ``=`` outside
    # of the shorthand's ``@`` separator. We prefer ``=`` when present.
    if "=" in stripped and not _is_at_shorthand(stripped):
        name_part, _, rhs = stripped.partition("=")
        name = name_part.strip()
        if not name:
            raise FragmentError(
                f"bad Rust dep spec (empty name): {dep!r}",
                code=FRAGMENT_DEP_SPEC_INVALID,
                context={"dep": dep, "reason": "empty_name"},
            )
        try:
            parsed = tomlkit.parse(f"__v = {rhs.strip()}")
        except Exception as e:  # noqa: BLE001
            raise FragmentError(
                f"bad Rust dep value in {dep!r}: {e}",
                code=FRAGMENT_DEP_SPEC_INVALID,
                context={"dep": dep, "reason": "toml_parse_failure"},
            ) from e
        return name, parsed["__v"]
    if "@" in stripped:
        name, version = stripped.split("@", 1)
        return name.strip(), version.strip()
    return stripped, "*"


def _is_at_shorthand(dep: str) -> bool:
    """True if `dep` looks like ``name@version`` — no ``=`` before the ``@``."""
    if "@" not in dep:
        return False
    at = dep.index("@")
    eq = dep.find("=")
    return eq == -1 or eq > at
