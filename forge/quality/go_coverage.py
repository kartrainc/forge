"""Translate Go coverprofiles into LCOV for the shared coverage gate."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

_REPLACE = re.compile(r"^\s*(?:replace\s+)?(\S+)(?:\s+\S+)?\s+=>\s+(\.{1,2}/\S*)\s*$")
_NON_EXECUTABLE = {"", "{", "}", ")", "})", "},", "default:"}
_SKIPPED_PARTS = {"tests", "test", "testdata", "vendor"}


def go_modules(service: Path) -> dict[str, Path]:
    """Map the service module and its local ``replace`` targets to directories."""
    text = (service / "go.mod").read_text(encoding="utf-8")
    modules: dict[str, Path] = {}
    in_replace = False
    for raw in text.splitlines():
        line = raw.split("//", 1)[0].rstrip()
        stripped = line.strip()
        if stripped.startswith("module "):
            modules[stripped.split()[1]] = service
        elif stripped == "replace (":
            in_replace = True
        elif in_replace and stripped == ")":
            in_replace = False
        elif (in_replace or stripped.startswith("replace ")) and (match := _REPLACE.match(line)):
            modules[match[1]] = (service / match[2]).resolve()
    if not modules:
        raise ValueError(f"{service / 'go.mod'}: missing module directive")
    return modules


def _source_path(name: str, modules: dict[str, Path]) -> Path | None:
    """Resolve an import-path file name; test helpers and vendored code are not
    application source."""
    for module in sorted(modules, key=len, reverse=True):
        if name.startswith(module + "/"):
            relative = PurePosixPath(name[len(module) + 1 :])
            if set(relative.parts) & _SKIPPED_PARTS:
                return None
            return modules[module] / relative
    return None


def go_profiles_to_lcov(profiles: list[Path], modules: dict[str, Path], output: Path) -> None:
    """Merge coverprofiles (max hits per line) and write LCOV.

    Go records statement blocks; each block marks its source lines, skipping
    blank, comment-only and bracket-only lines.
    """
    hits: dict[Path, dict[int, int]] = {}
    sources: dict[Path, list[str]] = {}
    for profile in profiles:
        for entry in profile.read_text(encoding="utf-8").splitlines():
            if not entry or entry.startswith("mode:"):
                continue
            location, statements, count = entry.rsplit(" ", 2)
            name, span = location.rsplit(":", 1)
            path = _source_path(name, modules)
            if path is None:
                continue
            lines = hits.setdefault(path, {})
            if int(statements) == 0:
                continue
            if path not in sources:
                sources[path] = path.read_text(encoding="utf-8").splitlines()
            start, end = (int(part.split(".")[0]) for part in span.split(","))
            for number in range(start, end + 1):
                text = sources[path][number - 1].strip()
                if text in _NON_EXECUTABLE or text.startswith("//"):
                    continue
                lines[number] = max(lines.get(number, 0), int(count))
    with output.open("w", encoding="utf-8") as stream:
        for path in sorted(hits):
            stream.write(f"SF:{path}\n")
            for number, count in sorted(hits[path].items()):
                stream.write(f"DA:{number},{count}\n")
            stream.write("end_of_record\n")
