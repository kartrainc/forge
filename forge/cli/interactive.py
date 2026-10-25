"""Interactive prompt flow for `forge` invoked without flags.

Uses questionary for the TUI. Tests patch ``forge.cli.questionary`` and
the ``_ask_*`` helpers via the re-exports on ``forge.cli``; both paths
(the re-export and the module-local name) resolve to the same callable.

This module also exposes :func:`prompt_harvest_candidate` — the
``--harvest-interactive`` review prompt wired by Theme 2C. The
harvester injects it as a ``prompt_callback`` and tests substitute a
deterministic deque-driven stub.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Literal, cast

import questionary

from forge.cli.parser import COLOR_SCHEMES
from forge.config import (
    BACKEND_REGISTRY,
    DEFAULT_REALM,
    BackendConfig,
    FrontendConfig,
    FrontendFramework,
    ProjectConfig,
    keycloak_client_id_from,
    validate_features,
)

if TYPE_CHECKING:
    from forge.extractors.pipeline import CandidatePatch


def _ask_text(message: str, default: str = "") -> str:
    value = questionary.text(message, default=default).ask()
    if value is None:
        sys.exit(1)
    return value


def _ask_confirm(message: str, default: bool = True) -> bool:
    value = questionary.confirm(message, default=default).ask()
    if value is None:
        sys.exit(1)
    return value


def _ask_select(message: str, choices: list[str]) -> str:
    value = questionary.select(message, choices=choices).ask()
    if value is None:
        sys.exit(1)
    return value


def _parse_features(raw: str) -> list[str]:
    return [f.strip() for f in raw.split(",") if f.strip()]


def _ask_features() -> list[str]:
    while True:
        raw = _ask_text(
            "CRUD entities to generate (comma-separated, e.g. items, orders):",
            default="items",
        )
        features = _parse_features(raw)
        if not features:
            print("  Please enter at least one feature.")
            continue
        try:
            validate_features(features)
        except ValueError as e:
            print(f"  Invalid: {e}")
            continue
        return features


def _ask_port(message: str, default: str) -> int:
    while True:
        raw = _ask_text(message, default=default)
        try:
            port = int(raw)
            if not (1024 <= port <= 65535):
                raise ValueError
            return port
        except ValueError:
            print("  Port must be a number between 1024 and 65535.")


def _prompt_backend(
    index: int,
    project_name: str,
    description: str,
    default_port: int,
) -> BackendConfig:
    """Prompt the user for one backend's configuration.

    Drives language and version choices from BACKEND_REGISTRY so adding a 4th
    backend doesn't require touching this function.
    """
    default_name = "backend" if index == 0 else f"backend-{index}"
    name = _ask_text("Backend name:", default=default_name)
    label_to_lang = {spec.display_label: lang for lang, spec in BACKEND_REGISTRY.items()}
    chosen_label = _ask_select("Backend language:", choices=list(label_to_lang.keys()))
    language = label_to_lang[chosen_label]
    spec = BACKEND_REGISTRY[language]
    port = _ask_port("Backend server port:", default=str(default_port))
    features = _ask_features()

    # ``spec.version_field`` is a real ``BackendConfig`` attribute only for the
    # built-ins (python_version / node_version / rust_edition). A plugin
    # language's version field (e.g. ``go_version``) isn't a dataclass field
    # and has nowhere to be stored, so we DON'T prompt for it — asking and then
    # silently dropping the answer would be misleading. The plugin template's
    # own copier default (surfaced via the spec's first version choice) applies.
    from dataclasses import fields as _dc_fields  # noqa: PLC0415

    version_kwargs: dict[str, str] = {}
    if spec.version_field in {f.name for f in _dc_fields(BackendConfig)}:
        version = _ask_select(f"{spec.display_label} version:", choices=list(spec.version_choices))
        version_kwargs = {spec.version_field: version}
    return BackendConfig(
        name=name,
        project_name=project_name,
        language=language,  # ty:ignore[invalid-argument-type]
        description=description,
        features=features,
        server_port=port,
        # Dynamic kwarg: ``spec.version_field`` is a runtime str (python_version /
        # node_version / rust_edition), all ``str`` fields — ty can't resolve the
        # dynamic key so it checks ``str`` against the widest field; suppress as
        # with ``language=`` above.
        **version_kwargs,  # ty:ignore[invalid-argument-type]
    )


def _collect_inputs() -> ProjectConfig | None:
    # Fail fast if no terminal is available
    if not sys.stdin.isatty():
        print(
            "Error: Interactive mode requires a terminal.\n"
            "Use --config, --yes, or --json for headless mode.",
            file=sys.stderr,
        )
        sys.exit(2)

    print()
    print("  +===================================+")
    print("  |             forge                  |")
    print("  |      Project Generator             |")
    print("  +===================================+")
    print()

    project_name = _ask_text("Project name:", default="My Platform")
    description = _ask_text("Description:", default="A full-stack application")

    backends: list[BackendConfig] = []
    print()
    print("  -- Backend 1 --")
    backends.append(_prompt_backend(0, project_name, description, default_port=5000))

    while _ask_confirm("Add another backend?", default=False):
        print()
        print(f"  -- Backend {len(backends) + 1} --")
        backends.append(
            _prompt_backend(
                len(backends),
                project_name,
                description,
                default_port=5000 + len(backends),
            )
        )

    print()
    print("  -- Frontend --")
    from forge.config import (  # noqa: PLC0415
        FRONTEND_SPECS,
        _PluginFramework,
        resolve_frontend_framework,
    )

    fw_map: dict[str, FrontendFramework | _PluginFramework] = {
        "Vue 3": FrontendFramework.VUE,
        "Svelte 5": FrontendFramework.SVELTE,
        "Flutter": FrontendFramework.FLUTTER,
        "None": FrontendFramework.NONE,
    }
    # Append any plugin-registered frontends (their display_label → sentinel)
    # so they're selectable interactively, not just via --frontend/--config.
    for value, spec in FRONTEND_SPECS.items():
        fw_map[spec.display_label] = resolve_frontend_framework(value)
    fw_choice = _ask_select("Frontend framework:", choices=list(fw_map.keys()))
    framework = fw_map[fw_choice]

    frontend: FrontendConfig | None = None
    include_auth = False

    if framework != FrontendFramework.NONE:
        author_name = _ask_text("Author name:", default="Your Name")

        pkg_choices = {
            FrontendFramework.VUE: ["npm", "pnpm", "yarn"],
            FrontendFramework.SVELTE: ["npm", "pnpm", "bun"],
            FrontendFramework.FLUTTER: [],
        }
        # A plugin frontend declares its package manager on its FrontendSpec;
        # built-ins prompt from the known set.
        if isinstance(framework, _PluginFramework):
            spec = FRONTEND_SPECS.get(framework.value)
            pkg_manager = spec.package_manager if spec is not None else "npm"
        else:
            pkg_manager = "npm"
            choices = pkg_choices.get(framework, [])
            if choices:
                pkg_manager = _ask_select("Package manager:", choices=choices)

        fe_port = 5173
        if framework != FrontendFramework.FLUTTER:
            fe_port = _ask_port("Frontend server port:", default="5173")

        include_auth = _ask_confirm("Enable Keycloak authentication?", default=True)

        # UI kit — only prompt when the framework offers a choice. Chat, the
        # colour schemes and the layouts offered next depend on it.
        from forge.ui_kits import (  # noqa: PLC0415
            DEFAULT_UI_KIT,
            applies_to,
            available_ui_kits,
            get_ui_kit,
        )

        ui_kit = DEFAULT_UI_KIT
        _kits = available_ui_kits(framework)
        if len(_kits) > 1:
            _kit_labels = {
                kit.display_label: kit.name
                for kit in (get_ui_kit(_n) for _n in _kits)
                if kit is not None
            }
            ui_kit = _kit_labels[_ask_select("UI kit:", choices=list(_kit_labels))]
        _kit = get_ui_kit(ui_kit)

        include_chat = False
        if _kit is None or _kit.chat:
            include_chat = _ask_confirm("Enable AI chat panel?", default=False)
        include_openapi = False
        if framework == FrontendFramework.VUE:
            include_openapi = _ask_confirm("Enable OpenAPI code generation?", default=False)
        elif framework == FrontendFramework.FLUTTER:
            # Flutter's retrofit-generated client is wired into the home feature,
            # so OpenAPI generation is mandatory (see FrontendConfig.validate).
            include_openapi = True

        color_scheme = "blue"
        if framework == FrontendFramework.VUE:
            color_scheme = _ask_select(
                "Default color scheme:",
                choices=list(_kit.color_schemes) if _kit is not None else COLOR_SCHEMES,
            )

        org_name = "com.example"
        if framework == FrontendFramework.FLUTTER:
            org_name = _ask_text("Organization name (reverse domain):", default="com.example")

        # App-shell layout — only prompt when the framework offers a choice
        # (all three built-in frameworks ship the full set of layouts).
        from forge.layout_variants import (  # noqa: PLC0415
            DEFAULT_LAYOUT,
            available_layouts,
            get_layout_variant,
        )

        layout = DEFAULT_LAYOUT
        _layouts = available_layouts(framework)
        if applies_to(framework):
            _layouts = tuple(
                _name
                for _name in _layouts
                if ui_kit in getattr(get_layout_variant(framework, _name), "ui_kits", ())
            )
        if len(_layouts) > 1:
            _labels: dict[str, str] = {}
            for _name in _layouts:
                _variant = get_layout_variant(framework, _name)
                if _variant is not None:
                    _labels[_variant.display_label] = _name
            _picked = _ask_select("App-shell layout:", choices=list(_labels))
            layout = _labels[_picked]

        frontend = FrontendConfig(
            # cast: a plugin framework is a _PluginFramework sentinel that
            # behaves like a FrontendFramework member at runtime.
            framework=cast("FrontendFramework", framework),
            project_name=project_name,
            description=description,
            author_name=author_name,
            package_manager=pkg_manager,
            include_auth=include_auth,
            include_chat=include_chat,
            include_openapi=include_openapi,
            server_port=fe_port,
            default_color_scheme=color_scheme,
            org_name=org_name,
            layout=layout,
            ui_kit=ui_kit,
        )

    include_keycloak = include_auth
    keycloak_port = 8080
    kc_url = "http://localhost:8080"
    kc_realm = "master"
    kc_client_id = ""

    if include_keycloak:
        print()
        print("  -- Keycloak --")
        keycloak_port = _ask_port("Keycloak host port:", default="18080")
        kc_url = f"http://localhost:{keycloak_port}"
        kc_realm = _ask_text("Keycloak realm:", default=DEFAULT_REALM)
        kc_client_id = _ask_text(
            "Keycloak client ID:",
            default=keycloak_client_id_from(project_name),
        )

    if frontend and include_keycloak:
        frontend.keycloak_url = kc_url
        frontend.keycloak_realm = kc_realm
        frontend.keycloak_client_id = kc_client_id

    config = ProjectConfig(
        project_name=project_name,
        backends=backends,
        frontends=([frontend] if frontend is not None else []),
        include_keycloak=include_keycloak,
        keycloak_port=keycloak_port,
    )

    _print_summary(config)

    if not _ask_confirm("Proceed with generation?"):
        return None

    try:
        config.validate()
    except ValueError as e:
        print(f"\n  Configuration error: {e}")
        return None

    return config


def _print_summary(config: ProjectConfig) -> None:
    print()
    print("  -- Summary --")
    print(f"  Project:    {config.project_name}")
    if config.backend:
        print(
            f"  Backend:    Python {config.backend.python_version} on port {config.backend.server_port}"
        )
    if config.frontend and config.frontend.framework != FrontendFramework.NONE:
        fw = config.frontend.framework.value.capitalize()
        if config.frontend.framework != FrontendFramework.FLUTTER:
            fw += f" on port {config.frontend.server_port}"
        print(f"  Frontend:   {fw}")
        print(f"  Features:   {', '.join(config.all_features)}")
    else:
        print("  Frontend:   None")
    print(f"  Auth:       {'Keycloak' if config.include_keycloak else 'Disabled'}")
    if config.include_keycloak:
        print(f"  Keycloak:   port {config.keycloak_port}")
    print()


# ---------------------------------------------------------------------------
# Theme 2C — --harvest-interactive review prompt
# ---------------------------------------------------------------------------

# Limit the in-line diff preview to a sensible number of lines so the
# prompt stays scannable. The full diff is one keystroke away
# ("[v]iew full diff").
_DIFF_PREVIEW_LINES = 12


def _short_diff_stat(diff: str) -> str:
    """Count ``+``/``-`` data lines in a unified diff.

    Skips the header lines (``+++`` / ``---``) and the hunk markers
    (``@@``) so the stat reflects content changes only. Returns a
    string like ``"+3 -1"`` or ``"(no diff)"`` when ``diff`` is empty.
    """
    if not diff:
        return "(no diff)"
    adds = 0
    dels = 0
    for line in diff.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            adds += 1
        elif line.startswith("-"):
            dels += 1
    return f"+{adds} -{dels}"


def _format_candidate_header(cand: CandidatePatch, index: int, total: int) -> str:
    """Render the one-screen candidate summary shown above the prompt."""
    lines = [
        "",
        f"  Candidate {index} of {total}",
        f"  Fragment: {cand.fragment}  (backend: {cand.backend})",
        f"  File:     {cand.target_path or cand.rel_path}",
        f"  Kind:     {cand.kind}  risk: {cand.risk}",
        f"  Diff:     {_short_diff_stat(cand.diff)}",
    ]
    if cand.rationale:
        lines.append(f"  Note:     {cand.rationale}")
    if cand.diff:
        preview = cand.diff.splitlines()[:_DIFF_PREVIEW_LINES]
        lines.append("")
        for raw in preview:
            lines.append(f"    {raw}")
        if len(cand.diff.splitlines()) > _DIFF_PREVIEW_LINES:
            remaining = len(cand.diff.splitlines()) - _DIFF_PREVIEW_LINES
            lines.append(f"    ... ({remaining} more line(s) — choose 'view full diff')")
    return "\n".join(lines)


def _print_full_diff(cand: CandidatePatch) -> None:
    """Render the full unified diff to stdout, untruncated."""
    print()
    print(f"  -- Full diff for {cand.target_path or cand.rel_path} --")
    if cand.diff:
        print(cand.diff)
    else:
        print("  (extractor did not emit a diff for this candidate)")
    print()


def prompt_harvest_candidate(
    cand: CandidatePatch,
    index: int,
    total: int,
) -> Literal["accept", "skip", "quit"]:
    """Interactive ``--harvest-interactive`` review prompt for one candidate.

    Prints a one-screen summary (file, kind, diff stat, short preview)
    then asks accept / skip / view-full-diff / quit. Selecting
    ``view full diff`` renders the untruncated diff and re-prompts on
    the same candidate; the loop only terminates on one of the three
    final decisions.

    Mirrors the ``_ask_select`` UX used elsewhere in this module so a
    headless CI environment (no TTY) surfaces the same ``sys.exit(1)``
    path that the project-generation prompt does. Tests inject a
    callable with the same signature directly into
    :func:`forge.sync.project_to_forge.harvester.harvest_project` and
    never hit this real-questionary path.
    """
    while True:
        print(_format_candidate_header(cand, index, total))
        choice = questionary.select(
            "Decision:",
            choices=[
                "accept",
                "skip",
                "view full diff",
                "quit",
            ],
        ).ask()
        if choice is None:
            # The user dismissed the prompt (Ctrl-C / EOF). Treat it as
            # quit so the harvester aborts cleanly rather than skipping
            # silently.
            return "quit"
        if choice == "view full diff":
            _print_full_diff(cand)
            continue
        if choice == "accept":
            return "accept"
        if choice == "skip":
            return "skip"
        if choice == "quit":
            return "quit"
        # Defensive: unknown selection (questionary shouldn't return
        # anything outside ``choices=``) — re-prompt rather than crash.
        continue
