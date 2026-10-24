"""DepsExtractor — reverse counterpart of :mod:`forge.appliers.deps`.

Where :class:`forge.appliers.deps.FragmentDepsApplier` merges
fragment-declared dependency strings into ``pyproject.toml`` /
``package.json`` / ``Cargo.toml``, this extractor reads those same
manifests and harvests:

* User-pinned versions that drift from the fragment-declared spec
  (e.g. fragment says ``slowapi>=0.1.9``, manifest says
  ``slowapi==0.1.11``).
* Removed deps — fragment-declared but no longer present.
* Added deps — present in the manifest but not in any fragment plan,
  signalling the operator added a hand-rolled dependency that may
  belong upstream.

Phase 4 implements the dep-level harvest by computing the set diff
between the fragment's declared specs and the on-disk manifest. The
decision is intentionally coarser than the file-level merge: every
divergence (add / remove / modify) is surfaced as ``"needs-review"``
because the maintainer review will encode the policy (was this a
hand-roll that belongs upstream? was the bump intentional?). Auto-
promotion is out of scope for Phase 4.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import tomlkit
import tomlkit.exceptions

from forge.config import BackendLanguage
from forge.extractors.pipeline import CandidatePatch, ExtractorKind

if TYPE_CHECKING:
    from pathlib import Path

    from forge.extractors.plan import ExtractionPlan
    from forge.fragment_context import FragmentContext


class DepsExtractor:
    """Harvest dependency drift from package manifests."""

    kind: ExtractorKind = "deps"

    def extract(
        self,
        ctx: FragmentContext,
        plan: ExtractionPlan,
    ) -> list[CandidatePatch]:
        """Return harvested candidates for ``plan.dependencies``.

        Walks the per-language manifest under ``ctx.backend_dir``,
        diffs the parsed dep set against ``plan.dependencies``, and
        emits one :class:`~forge.extractors.pipeline.CandidatePatch`
        per added / removed / modified dep. Each candidate's
        ``diff`` is a small JSON describing the change.

        Skips silently when:

        * ``plan.dependencies`` is empty (nothing to compare against).
        * The backend's manifest file is missing — the project may not
          be using this language, or the file was deleted; either way
          the extractor has no anchor.

        Edge cases that *don't* emit:

        * A fragment-declared dep that's present at the same spec — no
          drift.
        * Specs that differ only in whitespace or comparator style
          (``slowapi>=0.1.9`` vs ``slowapi >= 0.1.9``) collapse via
          name+version canonicalisation; the dep canonical form is what
          we compare, not the raw spec string.
        """
        if not plan.dependencies:
            return []

        lang = ctx.backend_config.language
        manifest_path = _manifest_path(ctx.backend_dir, lang)
        if manifest_path is None or not manifest_path.is_file():
            return []

        try:
            project_deps = _read_project_deps(manifest_path, lang)
        except (ValueError, tomlkit.exceptions.TOMLKitError, json.JSONDecodeError):
            # Malformed manifest — the forward applier would have
            # raised here, but the extractor is read-only. Surface a
            # single "needs-review" candidate pointing at the manifest
            # so the operator notices, rather than silently dropping
            # all the deps.
            return [
                CandidatePatch(
                    fragment=plan.fragment_name,
                    backend=ctx.backend_config.name,
                    kind="deps",
                    rel_path=manifest_path.name,
                    target_path=str(manifest_path),
                    diff=f"manifest parse error: {manifest_path.name}",
                    baseline_sha=None,
                    current_sha="",
                    risk="needs-review",
                    rationale="dependency manifest could not be parsed",
                )
            ]

        fragment_deps = _parse_fragment_deps(plan.dependencies, lang)

        candidates: list[CandidatePatch] = []

        project_names = set(project_deps)
        fragment_names = set(fragment_deps)

        # Removed: declared by fragment, missing from manifest.
        for name in sorted(fragment_names - project_names):
            spec = fragment_deps[name]
            candidates.append(
                _mk_candidate(
                    plan=plan,
                    ctx=ctx,
                    manifest_path=manifest_path,
                    action="removed",
                    name=name,
                    fragment_spec=spec,
                    project_spec=None,
                    rationale=(
                        f"fragment-declared dep '{name}' is missing from the project manifest"
                    ),
                )
            )

        # Added: in manifest, not declared by fragment.
        for name in sorted(project_names - fragment_names):
            spec = project_deps[name]
            candidates.append(
                _mk_candidate(
                    plan=plan,
                    ctx=ctx,
                    manifest_path=manifest_path,
                    action="added",
                    name=name,
                    fragment_spec=None,
                    project_spec=spec,
                    rationale=(f"project manifest carries dep '{name}' that no fragment declares"),
                )
            )

        # Modified: same name, different version pin / spec.
        for name in sorted(fragment_names & project_names):
            f_spec = fragment_deps[name]
            p_spec = project_deps[name]
            if f_spec == p_spec:
                continue
            candidates.append(
                _mk_candidate(
                    plan=plan,
                    ctx=ctx,
                    manifest_path=manifest_path,
                    action="modified",
                    name=name,
                    fragment_spec=f_spec,
                    project_spec=p_spec,
                    rationale=(
                        f"dep '{name}' spec drifted: fragment={f_spec!r} project={p_spec!r}"
                    ),
                )
            )

        return candidates


def _manifest_path(backend_dir: Path, lang: BackendLanguage) -> Path | None:
    """Return the per-language dependency-manifest path under ``backend_dir``."""
    if lang is BackendLanguage.PYTHON:
        return backend_dir / "pyproject.toml"
    if lang is BackendLanguage.NODE:
        return backend_dir / "package.json"
    if lang is BackendLanguage.RUST:
        return backend_dir / "Cargo.toml"
    if lang is BackendLanguage.GO:
        return backend_dir / "go.mod"
    return None


def _read_project_deps(manifest_path: Path, lang: BackendLanguage) -> dict[str, str]:
    """Parse the manifest into ``{canonical_name: spec_string}``.

    Spec format normalisation:

    * **Python** — value is the original PEP 508 spec stripped of
      surrounding whitespace; key is the lowercased package name (PEP
      503 normalisation light — we strip extras / version specifiers
      but don't canonicalise dashes-vs-underscores; the forward applier
      doesn't either).
    * **Node** — value is the npm version range as stored
      (``"^1.2.3"``, ``"workspace:*"``, ``"file:..."``); key is the
      package name verbatim (scoped or unscoped).
    * **Go** — value is the exact required version; key is the module path.
      ``// indirect`` requirements are resolver output, not declared deps.
    * **Rust** — value is the version string for shorthand entries; for
      inline-table entries (``{ version = "...", features = [...] }``)
      the value is the version field's string form. The full table
      form isn't round-tripped here — extractor reports drift at the
      version level only; richer spec drift surfaces as a "modified"
      candidate.
    """
    if lang is BackendLanguage.PYTHON:
        return _read_python_deps(manifest_path)
    if lang is BackendLanguage.NODE:
        return _read_node_deps(manifest_path)
    if lang is BackendLanguage.RUST:
        return _read_rust_deps(manifest_path)
    if lang is BackendLanguage.GO:
        return _read_go_deps(manifest_path)
    return {}


def _read_python_deps(pyproject: Path) -> dict[str, str]:
    doc = tomlkit.parse(pyproject.read_text(encoding="utf-8"))
    project = doc.get("project")
    if project is None:
        return {}
    raw = project.get("dependencies") or []
    out: dict[str, str] = {}
    for entry in list(raw):
        spec = str(entry).strip()
        if not spec:
            continue
        name = _py_dep_name(spec)
        out[name] = spec
    return out


def _read_node_deps(package_json: Path) -> dict[str, str]:
    data = json.loads(package_json.read_text(encoding="utf-8"))
    deps_obj = data.get("dependencies") or {}
    if not isinstance(deps_obj, dict):
        return {}
    return {str(k): str(v) for k, v in deps_obj.items()}


def _read_rust_deps(cargo_toml: Path) -> dict[str, str]:
    doc = tomlkit.parse(cargo_toml.read_text(encoding="utf-8"))
    table = doc.get("dependencies") or {}
    out: dict[str, str] = {}
    for name, value in dict(table).items():
        if isinstance(value, str):
            out[str(name)] = str(value).strip()
            continue
        # Inline table or sub-table — pull the version field out as the
        # canonical spec. Rest of the table (features, default-features,
        # etc.) is implicitly part of the "spec" but we report drift at
        # the version-string level for now.
        out[str(name)] = _rust_value_version(value)
    return out


def _read_go_deps(go_mod: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    in_block = False
    for raw in go_mod.read_text(encoding="utf-8").splitlines():
        line, _, comment = raw.partition("//")
        line = line.strip()
        if in_block and line == ")":
            in_block = False
            continue
        if line == "require (":
            in_block = True
            continue
        if not in_block:
            if not line.startswith("require "):
                continue
            line = line.removeprefix("require ").strip()
        parts = line.split()
        if len(parts) == 2 and comment.strip() != "indirect":
            out[parts[0]] = parts[1]
    return out


def _rust_value_version(value: Any) -> str:
    """Extract the canonical version string from a parsed Cargo dep value.

    ``value`` may be a plain string (shorthand entries — handled by the
    caller) or a TOML table / inline-table that carries a ``version``
    field. Tables without a ``version`` (e.g. git deps) fall back to
    ``"*"`` — the extractor isn't trying to do source-of-truth dep
    resolution; it only needs *some* canonical form that round-trips
    to itself across the fragment / project pair.
    """
    getter = getattr(value, "get", None)
    if getter is None:
        return "*"
    version = getter("version")
    return str(version).strip() if version is not None else "*"


def _parse_fragment_deps(
    deps: tuple[str, ...],
    lang: BackendLanguage,
) -> dict[str, str]:
    """Parse fragment-declared deps into ``{name: spec}`` for the language.

    Mirrors the forward applier's parse functions but maps each entry
    to ``(canonical_name, spec)``. Spec strings are returned in the
    same shape :func:`_read_project_deps` returns, so equality
    comparisons are direct.
    """
    out: dict[str, str] = {}
    if lang is BackendLanguage.PYTHON:
        for dep in deps:
            spec = dep.strip()
            if not spec:
                continue
            name = _py_dep_name(spec)
            out[name] = spec
    elif lang is BackendLanguage.NODE:
        for dep in deps:
            name, version = _split_node_dep(dep)
            out[name] = version
    elif lang is BackendLanguage.RUST:
        for dep in deps:
            name, version = _split_rust_dep(dep)
            out[name] = version
    elif lang is BackendLanguage.GO:
        for dep in deps:
            name, _, version = dep.strip().rpartition("@")
            out[name] = version
    return out


def _py_dep_name(dep: str) -> str:
    """Lowercased PEP-508 package name. Mirrors ``forge.appliers.deps._py_dep_name``."""
    for sep in ("==", ">=", "<=", "~=", "!=", ">", "<", "["):
        if sep in dep:
            return dep.split(sep, 1)[0].strip().lower()
    return dep.strip().lower()


def _split_node_dep(dep: str) -> tuple[str, str]:
    """Split a Node dep spec into ``(name, version)``. Mirrors the applier."""
    if "@" in dep and not dep.startswith("@"):
        name, version = dep.split("@", 1)
        return name, version
    if dep.startswith("@"):
        head, _, tail = dep[1:].partition("@")
        if tail:
            return "@" + head, tail
        return dep, "latest"
    return dep, "latest"


def _split_rust_dep(dep: str) -> tuple[str, str]:
    """Split a Rust dep spec into ``(name, version)``.

    Supports shorthand ``name@version`` and full TOML inline form
    ``name = { version = "...", features = [...] }``. The full form's
    version field is returned as the canonical spec, mirroring how
    :func:`_read_rust_deps` collapses inline tables.
    """
    stripped = dep.strip()
    if "=" in stripped and not _is_at_shorthand(stripped):
        name_part, _, rhs = stripped.partition("=")
        name = name_part.strip()
        try:
            parsed = tomlkit.parse(f"__v = {rhs.strip()}")
            value = parsed["__v"]
        except Exception:  # noqa: BLE001
            return name, rhs.strip()
        if isinstance(value, str):
            return name, str(value).strip()
        return name, _rust_value_version(value)
    if "@" in stripped:
        name, version = stripped.split("@", 1)
        return name.strip(), version.strip()
    return stripped, "*"


def _is_at_shorthand(dep: str) -> bool:
    """Mirror of ``forge.appliers.deps._is_at_shorthand``."""
    if "@" not in dep:
        return False
    at = dep.index("@")
    eq = dep.find("=")
    return eq == -1 or eq > at


def _mk_candidate(
    *,
    plan: ExtractionPlan,
    ctx: FragmentContext,
    manifest_path: Path,
    action: str,
    name: str,
    fragment_spec: str | None,
    project_spec: str | None,
    rationale: str,
) -> CandidatePatch:
    """Build a ``deps`` candidate patch with a structured-JSON diff."""
    payload = {
        "action": action,
        "name": name,
        "fragment_spec": fragment_spec,
        "project_spec": project_spec,
    }
    diff = json.dumps(payload, sort_keys=True, indent=2)
    return CandidatePatch(
        fragment=plan.fragment_name,
        backend=ctx.backend_config.name,
        kind="deps",
        rel_path=manifest_path.name,
        target_path=str(manifest_path),
        diff=diff,
        baseline_sha=None,
        current_sha="",
        risk="needs-review",
        rationale=rationale,
    )
