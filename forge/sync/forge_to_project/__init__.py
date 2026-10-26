"""Forward direction: forge → project (the existing forge --update flow).

The forward flow re-applies fragment intent to a generated project:
copy files, run injections, add deps, append env vars. Phase 3 of the
bidirectional-sync plan moves the forward modules here from the
top-level ``forge/`` package.
"""

from forge.sync.forge_to_project.plan import (
    FilePlanEntry,
    UpdatePlanReport,
    plan_update,
)
from forge.sync.forge_to_project.reapply_baseline import (
    ReapplyBaselineEntry,
    ReapplyBaselineReport,
    reapply_baseline,
)
from forge.sync.forge_to_project.uninstaller import (
    UninstallOutcome,
    disabled_fragments,
    uninstall_fragment,
)
from forge.sync.forge_to_project.updater import (
    FrontendTarget,
    apply_features,
    apply_project_features,
    classify_project_state,
    frontend_targets,
    update_project,
)

__all__ = [
    # plan
    "FilePlanEntry",
    # reapply-baseline
    "ReapplyBaselineEntry",
    "ReapplyBaselineReport",
    "UninstallOutcome",
    "UpdatePlanReport",
    # updater
    "FrontendTarget",
    "apply_features",
    "apply_project_features",
    "classify_project_state",
    "disabled_fragments",
    "frontend_targets",
    "plan_update",
    "reapply_baseline",
    # uninstaller
    "uninstall_fragment",
    "update_project",
]
