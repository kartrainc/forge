"""Run a Vue frontend's browser suites under its content-security policy.

With ``security.csp`` on, nginx serves the production build under the policy
of :mod:`forge.frontend_csp`. This check builds the app, serves ``dist/`` the
same way (``scripts/serve-csp.mjs`` reads the policy from ``nginx.conf``, or
from the file a project names in ``csp-check.json``, and replaces the nonce
placeholder per response), runs the Playwright suites against it and fails on
any violation the browser reports. A connect-src violation also catches an
icon fetched from the Iconify API at runtime instead of bundled. The build
keeps the project's authentication setting; the suites answer the session in
the page. Which suites run, and with which builds, is the project's
``playwright.csp.config.ts`` to say.

The check also proves that reports reach it, since an empty report file
would otherwise pass: the script opens a canary page of its own once, under a
fixed policy that blocks the page's one script, whatever the project's policy
admits. The app's pages are served untouched. Without the canary's report the
check fails. A body the collector could not read as a report is recorded as
unreadable and fails the check like any other violation.

Two reports are admitted for a ``nuxt-ui`` project, each a ``<style>`` element a
Nuxt UI 4.12 dependency inserts without the nonce in Vue mode. The browser
blocks both without visible effect, and Nuxt UI offers no option to stop or
nonce them. A report is admitted only when its sample, the first 40 characters
the policy's ``'report-sample'`` lets the browser send, is exactly the
element's. The palette copy's text is also the real palette's, so the Nuxt UI
e2e suite asserts separately that the palette applies.

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

#: Each admitted report's sample (its first 40 characters) under ``nuxt-ui``.
ADMITTED_STYLE_SAMPLES = {
    "@layer theme {\n  :root, :host {\n  --ui-c": "Nuxt UI's temporary palette copy (Vue mode)",
    "*,*::before,*::after{-webkit-transition:": "VueUse's colour-mode transition guard",
}

#: The script of the canary page ``serve-csp.mjs`` opens to prove reporting.
CANARY = "/__csp-canary.js"

#: The key ``serve-csp.mjs`` records a body under when it is no report.
UNREADABLE = "unreadable-report"


def admitted(report: dict, ui_kit: str) -> str | None:
    """Name the known upstream element a report is about, or ``None``."""
    if ui_kit != "nuxt-ui":
        return None
    if report.get("effective-directive") != "style-src-elem":
        return None
    if report.get("blocked-uri") != "inline":
        return None
    return ADMITTED_STYLE_SAMPLES.get(report.get("script-sample", ""))


def is_canary(report: dict) -> bool:
    """True for the report of the canary script that proves reports arrive."""
    return str(report.get("blocked-uri", "")).endswith(CANARY)


def _serves_the_canary(service: Path) -> bool:
    """True when the project's ``scripts/serve-csp.mjs`` opens the canary page.

    A script forge wrote before the canary existed does not; an update brings
    the current one while the script is untouched, a sidecar when it is edited.
    """
    script = service / "scripts" / "serve-csp.mjs"
    return script.is_file() and "/__csp-canary" in script.read_text(encoding="utf-8")


def check_content_security(service: Path, output: Path, *, ui_kit: str = "shadcn") -> dict:
    """Build ``service``, run its browser suites under the policy, judge the reports.

    Passes only when Playwright exits cleanly with tests run, no failed or flaky
    test and no global error, the canary's report arrived (when the project's
    serve script opens the canary; an older one is a warning), and every other
    report is admitted for ``ui_kit``. Returns ``{"passed", "tests",
    "violations", "admitted", "canary"}``. A service without
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
    # The build keeps the project's authentication setting, as the browser
    # suites run it (forge.quality.runner.run_browser).
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
        # Not check=True: the reports are judged even when a test fails.
        completed = subprocess.run(
            ["npx", "--no-install", "playwright", "test", "--config", "playwright.csp.config.ts"],
            cwd=service,
            env=env,
            check=False,
            stdout=stream,
            stderr=sys.stderr,
        )
    result = json.loads(execution.read_text(encoding="utf-8"))
    statistics = result["stats"]
    # A global error (the web server, setup or teardown) is reported apart
    # from the test statistics.
    errors = result.get("errors") or []
    passed_tests = (
        completed.returncode == 0
        and not errors
        and not statistics.get("unexpected", 0)
        and not statistics.get("flaky", 0)
        and statistics.get("expected", 0) > 0
    )
    lines = reports.read_text(encoding="utf-8").splitlines() if reports.is_file() else []
    violations: list[dict] = []
    known: dict[str, int] = {}
    canary = 0
    for line in lines:
        report = json.loads(line)
        if UNREADABLE in report:
            violations.append({UNREADABLE: report[UNREADABLE]})
            continue
        if is_canary(report):
            canary += 1
            continue
        why = admitted(report, ui_kit)
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
    # A script that predates the canary cannot prove collection; that is a
    # warning naming the fix, not a failure, until the project takes the
    # current script.
    canary_required = _serves_the_canary(service)
    outcome = {
        "passed": passed_tests and (canary > 0 or not canary_required) and not violations,
        "tests": statistics.get("expected", 0),
        "failed_tests": statistics.get("unexpected", 0),
        "exit_code": completed.returncode,
        "errors": len(errors),
        "violations": violations,
        "admitted": known,
        "canary": canary,
    }
    if not canary and not canary_required:
        outcome["warning"] = (
            "scripts/serve-csp.mjs predates the canary, so the check cannot prove that reports "
            "reach it: take forge's current script (merge its .forge-merge sidecar, or delete "
            "it and run forge --update)"
        )
    elif not canary:
        outcome["error"] = (
            "no report reached the check: the canary page went unreported (Chromium did "
            "not start for scripts/serve-csp.mjs, or its reports never reached the collector; "
            "see the script's messages on stderr)"
        )
    return outcome
