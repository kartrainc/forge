"""Run a Vue frontend's browser suites under its content-security policy.

With ``security.csp`` on, nginx serves the production build under the policy
of :mod:`forge.frontend_csp`. This check builds the app, serves ``dist/`` the
same way (``scripts/serve-csp.mjs`` reads the policy from ``nginx.conf`` and
replaces the nonce placeholder per response), runs the Playwright suites
against it and fails on any violation the browser reports. A connect-src
violation also catches an icon fetched from the Iconify API at runtime instead
of bundled.

Two reports are admitted, each a ``<style>`` element a Nuxt UI 4.12 dependency
inserts without the nonce in Vue mode. The browser blocks both without visible
effect, and Nuxt UI offers no option to stop or nonce them:

* Nuxt UI's colour plugin writes a temporary copy of the palette before its
  head entry renders; the head entry, which carries the nonce, applies it.
* VueUse's ``useColorMode`` suppresses transitions while the mode changes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

#: The start of each admitted report's sample: the blocked stylesheet's text.
ADMITTED_STYLE_SAMPLES = {
    "@layer theme {": "Nuxt UI's temporary palette copy (Vue mode)",
    "*,*::before,*::after{-webkit-transition:": "VueUse's colour-mode transition guard",
}


def admitted(report: dict) -> str | None:
    """Name the known upstream element a report is about, or ``None``."""
    if report.get("effective-directive") != "style-src-elem":
        return None
    if report.get("blocked-uri") != "inline":
        return None
    sample = report.get("script-sample", "")
    return next(
        (why for start, why in ADMITTED_STYLE_SAMPLES.items() if sample.startswith(start)), None
    )


def check_content_security(service: Path, output: Path) -> dict:
    """Build ``service``, run its browser suites under the policy, judge the reports.

    Returns ``{"passed", "tests", "violations", "admitted"}``. A service without
    ``playwright.csp.config.ts`` (the option off) is reported as skipped.
    """
    if not (service / "playwright.csp.config.ts").is_file():
        return {"passed": True, "skipped": "security.csp is off"}
    output.mkdir(parents=True, exist_ok=True)
    reports = output / "csp-reports.jsonl"
    reports.unlink(missing_ok=True)
    env = os.environ.copy()
    env.pop("FORGE_BROWSER_COVERAGE", None)
    env.pop("FORGE_COVERAGE", None)
    # No gateway serves /auth/* here; the suites mock the API in the browser.
    env["VITE_AUTH_DISABLED"] = "true"
    env["FORGE_CSP_REPORT"] = str(reports)
    subprocess.run(
        ["npm", "run", "build"],
        cwd=service,
        env=env,
        check=True,
        stdout=sys.stderr,
        stderr=sys.stderr,
    )
    execution = output / "csp.execution"
    with execution.open("w", encoding="utf-8") as stream:
        subprocess.run(
            ["npx", "--no-install", "playwright", "test", "--config", "playwright.csp.config.ts"],
            cwd=service,
            env=env,
            check=False,
            stdout=stream,
            stderr=sys.stderr,
        )
    statistics = json.loads(execution.read_text(encoding="utf-8"))["stats"]
    passed_tests = (
        not statistics.get("unexpected", 0)
        and not statistics.get("flaky", 0)
        and statistics.get("expected", 0) > 0
    )
    lines = reports.read_text(encoding="utf-8").splitlines() if reports.is_file() else []
    violations: list[dict] = []
    known: dict[str, int] = {}
    for line in lines:
        report = json.loads(line)
        why = admitted(report)
        if why:
            known[why] = known.get(why, 0) + 1
        else:
            violations.append(
                {
                    key: report.get(key)
                    for key in (
                        "document-uri",
                        "effective-directive",
                        "blocked-uri",
                        "source-file",
                        "line-number",
                        "script-sample",
                    )
                }
            )
    return {
        "passed": passed_tests and not violations,
        "tests": statistics.get("expected", 0),
        "failed_tests": statistics.get("unexpected", 0),
        "violations": violations,
        "admitted": known,
    }
