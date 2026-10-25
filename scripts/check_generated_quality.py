"""Render, install and enforce real native gates for a reference application."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from forge.config import (
    BackendConfig,
    BackendLanguage,
    FrontendConfig,
    FrontendFramework,
    ProjectConfig,
)
from forge.generator import generate
from forge.quality.architecture import verify_architecture
from forge.quality.coverage import evaluate
from forge.quality.csp import check_content_security
from forge.quality.dependencies import dependencies
from forge.quality.model import subjects
from forge.quality.runner import run_suites


def check(
    language: str,
    destination: Path,
    *,
    ui_kit: str = "shadcn",
    auth: bool = False,
    features: list[str] | None = None,
) -> dict:
    frontend = language in {"vue", "svelte"}
    config = ProjectConfig(
        project_name=f"quality-{language}",
        backends=[
            BackendConfig(
                name="api",
                language=BackendLanguage.PYTHON if frontend else BackendLanguage(language),
                **({"features": features} if features else {}),
            )
        ],
        frontends=[
            FrontendConfig(
                project_name="frontend",
                framework=FrontendFramework(language),
                include_auth=auth,
                include_chat=False,
                include_openapi=False,
                ui_kit=ui_kit,
            )
        ]
        if frontend
        else [],
    )
    rendered = generate(config, quiet=True, dry_run=True)
    try:
        shutil.copytree(rendered, destination)
    finally:
        shutil.rmtree(rendered.parent)
    architecture = verify_architecture(destination)
    if not architecture["passed"]:
        raise ValueError(json.dumps(architecture))
    dependencies(destination, lock=True)
    dependencies(destination)
    run_suites(destination, subject=config.frontend_slug if frontend else None)
    reports = [
        json.loads(path.read_text()) for path in (destination / ".forge/coverage").glob("*/*.json")
    ]
    inventory = subjects(config, destination)
    if frontend:
        inventory = [item for item in inventory if item["language"] == language]
    result = evaluate(destination, reports, inventory)
    result["architecture"] = architecture
    if language == "vue":
        # The production build under the policy nginx sends (security.csp).
        service = destination / "apps" / config.frontend_slug
        security = check_content_security(
            service, destination / ".forge/coverage" / config.frontend_slug, ui_kit=ui_kit
        )
        result["content_security"] = security
        result["passed"] = result["passed"] and security["passed"]
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--language", choices=["python", "node", "rust", "go", "vue", "svelte"], required=True
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ui-kit", default="shadcn", help="Vue UI kit to render (forge.ui_kits).")
    parser.add_argument(
        "--auth", action="store_true", help="Render the frontend with authentication on."
    )
    parser.add_argument("--features", help="Comma-separated CRUD features (default: items).")
    args = parser.parse_args()
    result = check(
        args.language,
        args.output.resolve(),
        ui_kit=args.ui_kit,
        auth=args.auth,
        features=[f.strip() for f in args.features.split(",")] if args.features else None,
    )
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 12)
