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


# The reference app-shell application (ADR-015): two layouts, sub-menus (one item opening
# a route, one opening the placeholder route), an action, the page extras as the routes the
# menus open, every system page built in. With --auth off it renders without sign-in.
APP_SHELL_REFERENCE: dict = {
    "app_template": "app-shell",
    "features": [],
    "routing": {"landing": "home"},
    "extras": ["home", "profile", "settings"],
    "layouts": {
        "console": {
            "shell": "header-sidebar",
            # A route without :item: the placeholder items open it without params.
            "placeholder": "settings",
            "menus": {
                "primary": [
                    {
                        "items": [
                            {
                                "id": "overview",
                                "label": "Overview",
                                "icon": "i-lucide-house",
                                "to": "home",
                            },
                            {
                                "id": "sales",
                                "label": "Sales",
                                "icon": "i-lucide-shopping-cart",
                                "children": [
                                    {
                                        "id": "orders",
                                        "label": "Orders",
                                        "icon": "i-lucide-receipt",
                                        "to": "profile",
                                    },
                                    {"id": "invoices", "label": "Invoices", "badge": "invoices"},
                                ],
                            },
                        ]
                    },
                    {
                        "label": "Account",
                        "items": [
                            {
                                "id": "preferences",
                                "label": "Preferences",
                                "icon": "i-lucide-sliders-horizontal",
                                "to": "settings",
                            },
                        ],
                    },
                ],
                "account": [
                    {
                        "items": [
                            {
                                "id": "profile",
                                "label": "Profile",
                                "icon": "i-lucide-user",
                                "to": "profile",
                            },
                            {
                                "id": "sign-out",
                                "label": "Log out",
                                "icon": "i-lucide-log-out",
                                "action": "sign-out",
                            },
                        ]
                    }
                ],
                "footer": [{"items": [{"id": "support", "label": "Support"}]}],
            },
        },
        "staff": {
            "menus": {
                "primary": [
                    {
                        "items": [
                            {
                                "id": "staff-home",
                                "label": "Staff",
                                "icon": "i-lucide-shield",
                                "to": "home",
                            }
                        ]
                    }
                ]
            },
        },
    },
}


def check(
    language: str,
    destination: Path,
    *,
    ui_kit: str = "shadcn",
    auth: bool = False,
    features: list[str] | None = None,
    extra_frontends: list[tuple[str, str]] | None = None,
    app_template: str | None = None,
    frontend_app_template: str | None = None,
) -> dict:
    """Render the reference application for ``language`` and run its gates.

    ``extra_frontends`` adds further Vue apps beside the primary, as
    ``(name, ui_kit)`` pairs: each becomes its own quality subject.
    ``frontend_app_template`` ``app-shell`` renders the primary as
    :data:`APP_SHELL_REFERENCE`.
    """
    frontend = language in {"vue", "svelte"}
    shell = APP_SHELL_REFERENCE if frontend_app_template == "app-shell" else {}
    config = ProjectConfig(
        project_name=f"quality-{language}",
        backends=[
            BackendConfig(
                name="api",
                language=BackendLanguage.PYTHON if frontend else BackendLanguage(language),
                **({"features": features} if features else {}),
                **({"app_template": app_template} if app_template else {}),
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
                **shell,
            ),
            *(
                FrontendConfig(
                    project_name="frontend",
                    framework=FrontendFramework.VUE,
                    include_auth=auth,
                    include_chat=False,
                    include_openapi=False,
                    ui_kit=extra_kit,
                    name=name,
                    server_port=5174 + i,
                )
                for i, (name, extra_kit) in enumerate(extra_frontends or [])
            ),
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
    apps = [fc.name for fc in config.active_frontends]
    if frontend:
        for name in apps:
            run_suites(destination, subject=name)
    else:
        run_suites(destination, subject=None)
    reports = [
        json.loads(path.read_text()) for path in (destination / ".forge/coverage").glob("*/*.json")
    ]
    inventory = subjects(config, destination)
    if frontend:
        # Only the apps are checked; the backend behind them is not the subject.
        languages = {fc.framework.value for fc in config.active_frontends}
        inventory = [item for item in inventory if item["language"] in languages]
    result = evaluate(destination, reports, inventory)
    result["architecture"] = architecture
    # The production build of each Vue app under the policy nginx sends
    # (security.csp). One app reports its result directly, as before; several
    # report one result per app name.
    securities = {
        fc.name: check_content_security(
            destination / "apps" / fc.name,
            destination / ".forge/coverage" / fc.name,
            ui_kit=fc.ui_kit,
        )
        for fc in config.active_frontends
        if fc.framework == FrontendFramework.VUE
    }
    if securities:
        result["content_security"] = (
            next(iter(securities.values())) if len(securities) == 1 else securities
        )
        result["passed"] = result["passed"] and all(s["passed"] for s in securities.values())
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
    parser.add_argument(
        "--app-template",
        help="The backend's app template variant (default: the language's crud-service).",
    )
    parser.add_argument(
        "--frontend-app-template",
        choices=["crud-app", "app-shell"],
        help="The primary frontend's application template (default: crud-app).",
    )
    parser.add_argument(
        "--extra-frontend",
        action="append",
        default=[],
        metavar="NAME[:KIT]",
        help="Add a Vue app beside the primary (repeatable); KIT defaults to shadcn.",
    )
    args = parser.parse_args()
    result = check(
        args.language,
        args.output.resolve(),
        ui_kit=args.ui_kit,
        auth=args.auth,
        features=[f.strip() for f in args.features.split(",")] if args.features else None,
        extra_frontends=[
            (name, kit or "shadcn")
            for name, _, kit in (item.partition(":") for item in args.extra_frontend)
        ],
        app_template=args.app_template,
        frontend_app_template=args.frontend_app_template,
    )
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 12)
