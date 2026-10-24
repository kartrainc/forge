"""Strict native-report normalization and per-subject changed-line coverage."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from forge.quality.model import IGNORED, SOURCE_SUFFIXES, digest, project_path

SUITES = {"unit", "integration", "e2e"}


def _relative(name: str, source_root: Path, root: Path) -> str:
    path = Path(name)
    if not path.is_absolute():
        path = source_root / path
    relative = path.resolve().relative_to(root.resolve()).as_posix()
    project_path(root, relative)
    return relative


def native_lines(report: Path, source_root: Path, root: Path) -> dict[str, dict[int, int]]:
    """Read coverage.py JSON, Istanbul JSON, or LCOV; never infer a passing report."""
    result: dict[str, dict[int, int]] = {}
    if report.suffix in {".info", ".lcov"}:
        current: dict[int, int] | None = None
        for line in report.read_text(encoding="utf-8").splitlines():
            if line.startswith("SF:"):
                current = result.setdefault(_relative(line[3:], source_root, root), {})
            elif line.startswith("DA:") and current is not None:
                number, hits, *_ = line[3:].split(",")
                n, h = int(number), int(hits)
                if n <= 0 or h < 0:
                    raise ValueError("Invalid LCOV line counter")
                current[n] = max(current.get(n, 0), h)
            elif line == "end_of_record":
                current = None
    else:
        data = json.loads(report.read_text(encoding="utf-8"))
        if "files" in data and "meta" in data:  # coverage.py
            for name, entry in data["files"].items():
                lines = dict.fromkeys(entry["missing_lines"], 0)
                lines.update(dict.fromkeys(entry["executed_lines"], 1))
                result[_relative(name, source_root, root)] = lines
        else:  # Istanbul/Vitest
            for name, entry in data.items():
                lines = result.setdefault(_relative(entry.get("path", name), source_root, root), {})
                for key, statement in entry["statementMap"].items():
                    hits = entry["s"][key]
                    if not isinstance(hits, int) or hits < 0:
                        raise ValueError("Invalid Istanbul counter")
                    line = statement["start"]["line"]
                    lines[line] = max(lines.get(line, 0), hits)
    if not result:
        raise ValueError(f"Coverage report contains no source files: {report}")
    return result


def make_report(
    root: Path, subject: str, suite: str, native: Path, source_root: Path, *, tests: int
) -> dict:
    if suite not in SUITES or tests <= 0:
        raise ValueError("A required suite must execute at least one passing test")
    files = native_lines(native, source_root, root)
    return {
        "schema_version": 1,
        "subject": subject,
        "suite": suite,
        "tests": tests,
        "files": {
            rel: {"sha256": digest(project_path(root, rel)), "lines": lines}
            for rel, lines in files.items()
        },
    }


def changed_lines(root: Path, base_ref: str) -> dict[str, set[int]]:
    """Diff against an existing merge base, including local edits; no missing-base fallback."""
    base = subprocess.run(
        ["git", "merge-base", "HEAD", base_ref],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout.strip()
    names = subprocess.run(
        ["git", "diff", "--no-ext-diff", "--name-only", "-z", base, "--"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout
    result: dict[str, set[int]] = {}
    # Git quotes non-ASCII and control characters in diff headers. Obtain paths
    # through its NUL-delimited protocol so such files cannot escape the gate.
    for name in filter(None, names.split("\0")):
        path = project_path(root, name)
        if not path.is_file() or path.suffix not in SOURCE_SUFFIXES:
            continue
        diff = subprocess.run(
            ["git", "diff", "--no-ext-diff", "--no-textconv", "--unified=0", base, "--", name],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        ).stdout
        result[name] = set()
        for line in diff.splitlines():
            if match := re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line):
                start, count = int(match[1]), int(match[2] or 1)
                result[name].update(range(start, start + count))
    # Untracked application files are new code too, including local use before commit.
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout
    for name in filter(None, untracked.split("\0")):
        path_obj = project_path(root, name)
        if path_obj.is_file() and path_obj.suffix in SOURCE_SUFFIXES:
            result[name] = set(range(1, len(path_obj.read_text(encoding="utf-8").splitlines()) + 1))
    return result


def source_inventory(root: Path, subject_path: str) -> set[str]:
    base = project_path(root, subject_path)
    result: set[str] = set()
    # Go packages live anywhere under the module root (cmd/, internal/, ...).
    sources = (base,) if (base / "go.mod").is_file() else (base / "src", base / "lib")
    for source in sources:
        if not source.is_dir():
            continue
        for path in source.rglob("*"):
            rel = path.relative_to(base)
            if (
                path.is_file()
                and path.suffix in SOURCE_SUFFIXES
                and not set(rel.parts) & IGNORED
                and not set(rel.parts) & {"tests", "test", "__tests__"}
                and not any(x in path.name for x in (".test.", ".spec.", ".d.ts"))
                and not path.name.endswith("_test.go")
                and has_executable_source(path)
            ):
                result.add(path.relative_to(root).as_posix())
    return result


# Go coverage counts statements inside function bodies only. gofmt puts every
# top-level func at column zero; an empty body ({}) holds no statement.
_GO_FUNCTION_BODY = re.compile(
    r"^(?:func\b|.*=\s*func\s*\().*\{(?!\s*\}\s*(?://.*)?$)", re.MULTILINE
)


def has_executable_source(path: Path) -> bool:
    """LLVM omits declaration-only Rust files and Go omits files without
    functions: those have no coverage denominator."""
    if path.suffix == ".go":
        return bool(_GO_FUNCTION_BODY.search(path.read_text(encoding="utf-8")))
    if path.suffix != ".rs":
        return True
    import tree_sitter_rust
    from tree_sitter import Language, Parser

    tree = Parser(Language(tree_sitter_rust.language())).parse(path.read_bytes())
    if tree.root_node.has_error:
        return True  # Invalid syntax cannot be used to exempt a source file.
    pending = [tree.root_node]
    while pending:
        node = pending.pop()
        if node.type == "function_item" and node.child_by_field_name("body") is not None:
            return True
        pending.extend(node.named_children)
    return False


def evaluate(
    root: Path,
    reports: list[dict],
    subjects: list[dict],
    *,
    base_ref: str | None = None,
    required_suites: tuple[str, ...] = ("unit", "integration", "e2e"),
) -> dict:
    """Merge hits by file/line; enforce >80% separately for each subject.

    A fresh project has no base, so all executable lines are new. Existing
    projects gate changed lines and still report total coverage. Reports must
    describe the exact source currently on disk and every declared source.
    """
    delta = changed_lines(root, base_ref) if base_ref else None
    failures: list[str] = []
    metrics: list[dict[str, Any]] = []
    known = {subject["name"] for subject in subjects}
    # Shared runtime packages are independently gated from the actual tests
    # exercising them. Never let a well-tested service hide an untested SDK.
    expanded = list(reports)
    for subject in subjects:
        if not subject.get("shared"):
            continue
        prefix = subject["path"] + "/"
        for suite in required_suites:
            files: dict = {}
            tests = 0
            for report in reports:
                if report.get("suite") != suite:
                    continue
                matching = {
                    p: e for p, e in report.get("files", {}).items() if p.startswith(prefix)
                }
                if matching:
                    tests += report.get("tests", 0)
                for path, entry in matching.items():
                    if path not in files:
                        files[path] = {"sha256": entry["sha256"], "lines": dict(entry["lines"])}
                    else:
                        if files[path]["sha256"] != entry["sha256"]:
                            failures.append(f"{path}: inconsistent shared source hashes")
                        for line, hits in entry["lines"].items():
                            files[path]["lines"][line] = max(
                                files[path]["lines"].get(line, 0), hits
                            )
            if files:
                expanded.append(
                    {
                        "schema_version": 1,
                        "subject": subject["name"],
                        "suite": suite,
                        "tests": tests,
                        "files": files,
                    }
                )
    for report in reports:
        if report.get("subject") not in known:
            failures.append(f"Unknown report subject: {report.get('subject')}")
    for subject in subjects:
        name, subject_path = subject["name"], subject["path"]
        merged: dict[str, dict[int, int]] = {}
        seen: set[str] = set()
        stage_metrics: dict[str, dict] = {}
        for report in expanded:
            if report.get("subject") != name:
                continue
            suite = report.get("suite")
            if (
                report.get("schema_version") != 1
                or suite not in SUITES
                or type(report.get("tests")) is not int
                or report["tests"] <= 0
            ):
                failures.append(f"{name}: malformed or empty suite report")
                continue
            if suite in seen:
                failures.append(f"{name}: duplicate {suite} report; merge shards before gating")
            seen.add(suite)
            if not report.get("files"):
                failures.append(f"{name}/{suite}: missing coverage files")
            stage_covered = stage_total = 0
            for rel, entry in report.get("files", {}).items():
                path = project_path(root, rel)
                if not path.is_file() or digest(path) != entry.get("sha256"):
                    failures.append(f"{name}/{suite}: stale or missing source {rel}")
                    continue
                lines = merged.setdefault(rel, {})
                for raw_number, hits in entry.get("lines", {}).items():
                    number = int(raw_number)
                    if (
                        number < 1
                        or number > len(path.read_text(encoding="utf-8").splitlines())
                        or type(hits) is not int
                        or hits < 0
                    ):
                        raise ValueError(f"Invalid coverage counter: {rel}:{raw_number}")
                    lines[number] = max(lines.get(number, 0), hits)
                    stage_total += 1
                    stage_covered += hits > 0
            stage_metrics[suite] = {
                "tests": report["tests"],
                "covered": stage_covered,
                "total": stage_total,
            }
        for suite in required_suites:
            if suite not in seen:
                failures.append(f"{name}: missing required {suite} report")
        inventory = source_inventory(root, subject_path)
        for missing in sorted(inventory - merged.keys()):
            failures.append(f"{name}: source omitted from coverage: {missing}")
        total = covered = changed = changed_covered = 0
        for rel, lines in merged.items():
            if not rel.startswith(subject_path.rstrip("/") + "/"):
                continue  # Shared packages are evaluated as their own subjects.
            if any(
                s.get("shared") and s["name"] != name and rel.startswith(s["path"] + "/")
                for s in subjects
            ):
                continue
            for number, hits in lines.items():
                total += 1
                covered += hits > 0
                if delta is None or number in delta.get(rel, set()):
                    changed += 1
                    changed_covered += hits > 0
        if not total:
            failures.append(f"{name}: no executable source coverage")
        # Integer arithmetic makes exactly 80% fail without rounding ambiguity.
        if changed and changed_covered * 100 <= changed * 80:
            failures.append(
                f"{name}: new-code coverage {changed_covered}/{changed} must be greater than 80%"
            )
        metrics.append(
            {
                "subject": name,
                "covered": covered,
                "total": total,
                "new_covered": changed_covered,
                "new_total": changed,
                "suites": stage_metrics,
            }
        )
    if not subjects:
        failures.append("No coverage subjects declared")
    return {"passed": not failures, "subjects": metrics, "violations": failures}
