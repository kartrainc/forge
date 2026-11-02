"""Headless generated-code gates and workload recommendations."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from forge.errors import ForgeError


def run_quality(args: argparse.Namespace) -> int:
    root = Path(args.project_path or ".").resolve()
    try:
        if args.quality == "architecture":
            from forge.quality.architecture import verify_architecture

            result = verify_architecture(root)
        elif args.quality == "migrate":
            from forge.quality.update import migration_report

            result = migration_report(root)
        elif args.quality == "resolve":
            from forge.quality.update import resolve_owned_conflict

            if not args.subject or not args.resolution:
                raise ValueError(
                    "Conflict resolution requires --subject <relative file> and --resolution keep|replace"
                )
            result = resolve_owned_conflict(root, args.subject, resolution=args.resolution)
        elif args.quality == "inventory":
            from forge.quality.model import read_recipe, subjects

            result = {"subjects": subjects(read_recipe(root), root)}
        elif args.quality == "test":
            from forge.quality.runner import run_suites

            result = run_suites(root, suite=args.suite, subject=args.subject)
        elif args.quality in {"lock", "install"}:
            from forge.quality.dependencies import dependencies

            result = dependencies(root, lock=args.quality == "lock")
        else:
            from forge.quality.coverage import evaluate
            from forge.quality.model import read_recipe, subjects

            reports = [
                json.loads(path.read_text(encoding="utf-8"))
                for path in sorted((root / ".forge/coverage").glob("*/*.json"))
            ]
            # --subjects a,b: gate the subjects a CI run tested (a subset); absent, every subject
            selected = getattr(args, "subjects", None)
            only = (
                {name for name in selected.replace(" ", ",").split(",") if name}
                if selected is not None
                else None
            )
            result = evaluate(
                root,
                reports,
                subjects(read_recipe(root), root),
                base_ref=args.base_ref,
                only=only,
            )
        print(json.dumps(result, indent=2))
        return 0 if result.get("passed", True) else 12
    except (ValueError, OSError, KeyError, ForgeError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"passed": False, "error": str(exc)}))
        return 12


def run_recommend(args: argparse.Namespace) -> int:
    from forge.cli.loader import _load_config_file
    from forge.recommend import RecommendationRequest, recommend

    try:
        result = recommend(RecommendationRequest.model_validate(_load_config_file(args.recommend)))
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, OSError, ForgeError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 2
