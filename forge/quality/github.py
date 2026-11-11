"""GitHub Actions error annotations for failed gates and tests; silent outside Actions."""

from __future__ import annotations

import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

LIMIT = 20


def enabled() -> bool:
    return os.environ.get("GITHUB_ACTIONS") == "true"


def _data(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _property(text: str) -> str:
    return _data(text).replace(":", "%3A").replace(",", "%2C")


def error(title: str, message: str) -> None:
    if enabled():
        print(f"::error title={_property(title)}::{_data(message)}", file=sys.stderr)


def gate(name: str, result: dict) -> None:
    if result.get("passed", True):
        return
    problems = result.get("violations") or [result.get("error") or "The gate did not pass"]
    for problem in problems[:LIMIT]:
        error(f"Forge {name} gate", problem if isinstance(problem, str) else json.dumps(problem))
    if len(problems) > LIMIT:
        error(f"Forge {name} gate", f"{len(problems) - LIMIT} more violations in the step log")


def junit_failures(path: Path) -> None:
    """pytest has no GitHub reporter of its own; its JUnit report names each failed test."""
    if not enabled():
        return
    try:
        cases = list(ET.parse(path).getroot().iter("testcase"))
    except (OSError, ET.ParseError):
        return
    failed = [
        (case, problem)
        for case in cases
        for problem in [*case.iter("failure"), *case.iter("error")]
    ]
    for case, problem in failed[:LIMIT]:
        message = (problem.get("message") or problem.text or "failed").strip()
        error(f"{case.get('classname')}::{case.get('name')}", "\n".join(message.splitlines()[:10]))


def forward(path: Path) -> None:
    """Playwright's github reporter writes to stdout, which the browser suite keeps in a file."""
    if not enabled():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        if line.startswith("::"):
            print(line, file=sys.stderr)
