"""Read and write the project-root ``forge.toml`` manifest.

``forge.toml`` is the source of truth for ``forge --update``: it records
which forge version generated the project, where the templates live, and
every Option value the user set. From schema v2 (1.2.0+) it also
records per-language base-template versions, per-file template/fragment
versions, and per-block emitting-fragment metadata — the substrate the
bidirectional sync flows (forward update, reverse harvest, drift verify)
all share. From schema v3 (WS2) it adds a parallel
``[forge.option_origins]`` table that records, for every persisted
option, whether the value was user-set or defaulted by the resolver —
the substrate ``forge --update`` uses to skip fragments whose backends
aren't present without erroring on options the user never asked for.
From schema v4 (Initiative #3) it adds a ``[forge.frontend]`` table
recording the frontend framework + its on-disk app directory so
``forge --update`` can target frontend-only projects (``backend.mode =
"none"``) and gate ``target_frontends`` fragments without re-deriving
the framework from disk every run.

Canonical v4 format::

    [forge]
    schema_version = 4
    version = "1.2.0"
    project_name = "acme"

    [forge.templates]
    python = "services/python-service-template"
    vue = "apps/vue-frontend-template"

    [forge.template_versions]
    python = "0.6.1"
    vue = "1.0.0"

    [forge.frontend]
    framework = "vue"
    app_dir = "apps/frontend"

    [forge.options]
    "middleware.rate_limit" = true
    "rag.backend" = "qdrant"

    [forge.option_origins]
    "middleware.rate_limit" = "user"
    "rag.backend" = "default"

    [forge.provenance."src/app/main.py"]
    origin = "base-template"
    sha256 = "abc..."
    template_name = "python-service-template"
    template_version = "0.6.1"
    emitted_at = "2026-04-21T14:33:02Z"

    [forge.merge_blocks."src/app/main.py::middleware_cors:MIDDLEWARE_REGISTRATION"]
    sha256 = "def..."
    fragment_name = "middleware_cors"
    fragment_version = "1.2.0"
    snippet_sha256 = "789..."
    line_range = [44, 46]

Schema v1 manifests (missing ``schema_version``) are accepted on read —
the new fields default to absent. The next ``forge --update`` run
upgrades the manifest in place via ``migrate_provenance_v2``.

Schema v2 manifests are accepted on read — the absent
``[forge.option_origins]`` table is synthesized as all-``"default"`` so
the resolver silently skips fragments whose backends aren't present
(instead of erroring on persisted defaults). After the next
``forge --update`` (or first re-generate) the manifest is re-stamped
to v3 with accurate origins. Other write paths (``--reapply-baseline``,
``--accept-harvested``, ``--resolve``) preserve the on-disk schema
version intentionally — they shouldn't surprise the user with an
unrelated schema bump on what may be a quick fix-up command.

Schema v3 manifests are accepted on read — the absent
``[forge.frontend]`` table is reconstructed by walking the on-disk
project for the ``apps/<slug>/`` directory and a recognisable
framework marker (Vue / Svelte / Flutter), seeded from
``[forge.templates]`` when present (the same map the generator stamps).
This keeps ``forge --update`` working on pre-v4 projects without a
manual migration step; the next re-stamp upgrades the manifest in
place.

Pre-Option shapes (legacy ``[forge.features.*]`` /
``[forge.parameters]`` tables) are rejected with a clear error pointing
to ``forge.toml`` re-generation — the refactor is a hard cutover.
"""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomlkit

logger = logging.getLogger(__name__)

# Bumped from 3 to 4 in Initiative #3 to introduce a top-level
# ``[forge.frontend]`` table that records the frontend framework + its
# on-disk app directory. Pre-bump, ``forge --update`` had to re-derive
# the frontend from disk every run (or, worse, treat frontend-only
# projects as "no services found, bail"). v4 makes the frontend a
# first-class manifest fact so backend-less projects update cleanly
# and ``Fragment.target_frontends`` gating actually fires on update.
# v3 manifests still read (the missing table is reconstructed by
# walking ``apps/`` for a recognisable framework marker).
#
# Bumped from 2 to 3 in WS2 to introduce a parallel
# ``[forge.option_origins]`` table that records, for each persisted
# option, whether the value was user-set or defaulted by the resolver.
# Pre-bump, ``[forge.options]`` stored resolved values without
# distinguishing user intent — so ``forge --update`` couldn't tell
# user-set options apart from defaults and would error on persisted
# defaults whose fragment backends weren't present. v3 fixes that;
# v2 manifests still read (origins synthesized as all-default).
CURRENT_SCHEMA_VERSION = 4


@dataclass(frozen=True)
class ForgeFrontendData:
    """Frontend-related manifest data, persisted under ``[forge.frontend]``.

    ``framework`` is the value of the :class:`FrontendFramework` enum
    (``"vue"`` / ``"svelte"`` / ``"flutter"`` / ``"none"``) or a plugin-
    registered framework name. ``"none"`` means the project has no
    frontend; the field is still emitted so the manifest is explicit
    about layer composition (and so the updater can distinguish "no
    frontend" from "field missing because pre-v4 manifest").

    ``app_dir`` is the POSIX-style project-relative path to the
    generated frontend (typically ``"apps/frontend"``). Empty when
    ``framework == "none"``.

    Note: project-scope fragments that declare ``target_frontends``
    ship app-relative trees (e.g. ``files/src/Foo.vue``), which
    ``forge --update`` and ``--plan-update`` apply under
    ``project_root / app_dir``. ``app_dir`` is also consumed by the
    inference fallback (it picks up the on-disk slot when an older
    manifest is loaded) and by the template re-render driver
    (locating Copier's target dir).
    """

    framework: str = ""
    app_dir: str = ""
    # UI app-shell layout (``--layout`` / FrontendConfig.layout). Persisted so
    # ``forge update`` / harvest round-trips preserve the user's choice instead
    # of silently resetting to the default. Empty ⇒ pre-layout manifest (treat
    # as the default at the consume site).
    layout: str = ""
    # Vue UI kit (``--ui-kit`` / FrontendConfig.ui_kit), persisted for the same
    # reason as ``layout``. Empty ⇒ a manifest from before the option, or a
    # framework without kits: the default kit at the consume site.
    ui_kit: str = ""


@dataclass
class ForgeTomlData:
    """Parsed ``forge.toml`` contents.

    ``schema_version`` reflects the manifest's encoding: 1 (legacy,
    pre-1.2.0) lacks the version+timestamp fields on per-file and
    per-block entries; 2 (1.2.0+) carries the richer metadata that
    enables drift verify and reverse-direction harvest; 3 (WS2) adds
    per-option provenance via ``option_origins``; 4 (Initiative #3)
    adds the ``[forge.frontend]`` table. Manifests without an
    explicit version are interpreted as v1.
    """

    version: str
    project_name: str
    schema_version: int = CURRENT_SCHEMA_VERSION
    # Phase 4: the ``--platform`` preset the project was generated with (e.g.
    # "microservices"), or None when no preset was selected. Persisted under
    # ``[forge].platform_template`` and only when set, so no-platform projects
    # stay byte-identical.
    platform_template: str | None = None
    templates: dict[str, str] = field(default_factory=dict)
    # Per-language base-template semver at the time the project was
    # generated. Empty in v1 manifests. Populated in v2 from the
    # template's own version field at generate time.
    template_versions: dict[str, str] = field(default_factory=dict)
    # Dotted option path → value. Only paths the user explicitly set
    # appear here (the resolver fills in defaults).
    options: dict[str, Any] = field(default_factory=dict)
    # Dotted option path → origin tag ("user" / "default"). Parallel
    # to ``options`` — every key present in ``options`` should have a
    # corresponding entry here. v2 manifests synthesize this as
    # all-"default" on read (we can't recover the user's intent post
    # hoc); v3 manifests parse it directly. Missing entries in a v3
    # manifest default to "default" (tolerate partial writes).
    option_origins: dict[str, str] = field(default_factory=dict)
    # Per-path provenance for files the generator emitted. See
    # ``forge.sync.provenance`` for the recording / classification primitives.
    # Keys are POSIX-style relative paths; values are dicts whose keys
    # include ``{origin, sha256, fragment_name?, fragment_version?,
    # template_name?, template_version?, emitted_at?}``. v1 entries
    # carry only the first three.
    provenance: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Per-block baselines for merge-zone injections (1.0.0a3+).
    # Keys are ``{rel_path}::{feature_key}:{marker}``. Values include
    # ``{sha256, fragment_name?, fragment_version?, snippet_sha256?,
    # line_range?}``. v1 entries carry only ``{sha256}``.
    merge_blocks: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Frontend framework + app directory (v4+). On v3 reads this is
    # reconstructed from ``[forge.templates]`` + on-disk inference;
    # see :func:`_infer_frontend_from_v3` for the fallback logic.
    frontend: ForgeFrontendData = field(default_factory=ForgeFrontendData)


def read_forge_toml(path: Path) -> ForgeTomlData:
    """Parse ``forge.toml`` into a structured object.

    Raises ``FileNotFoundError`` if the file is missing and ``ValueError``
    on malformed content or legacy-shape tables. Manifests without an
    explicit ``schema_version`` are interpreted as schema v1; the
    structure they expose has empty ``template_versions`` and
    sparse entry sub-dicts. v2 manifests parse with synthesized
    ``option_origins`` (all-"default" — see the read-time migration
    note in the module docstring). v3 manifests parse origins from
    the dedicated table.

    ``schema_version`` is reported as found on disk — read does not
    re-stamp. The next ``forge --update`` (or ``forge generate``)
    re-stamps to ``CURRENT_SCHEMA_VERSION``.
    """
    if not path.is_file():
        raise FileNotFoundError(f"forge.toml not found at {path}")
    # Parse with stdlib tomllib, not tomlkit: the read path coerces everything
    # to plain Python via _coerce_*/_unwrap (style preservation is unused on
    # read; only write_forge_toml needs tomlkit, and it rebuilds the document
    # from scratch). tomlkit.parse is ~700x slower on a large provenance
    # manifest — ~10s on a 79KB forge.toml that tomllib reads in ~15ms — and
    # every sync verb (verify/harvest/update/accept/resolve) pays it.
    doc = tomllib.loads(path.read_text(encoding="utf-8"))

    forge = doc.get("forge")
    if forge is None:
        raise ValueError(f"{path}: missing [forge] section")

    # Reject legacy tables up front — do not silently auto-migrate.
    if "features" in forge:
        raise ValueError(
            f"{path}: found legacy [forge.features] table. This forge.toml "
            "was written by a pre-Option forge; the current forge uses "
            "[forge.options] exclusively. Regenerate the project with the "
            "current forge to migrate."
        )
    if "parameters" in forge:
        raise ValueError(
            f"{path}: found legacy [forge.parameters] table. This forge.toml "
            "was written by a pre-Option forge; the current forge uses "
            "[forge.options] exclusively. Regenerate the project with the "
            "current forge to migrate."
        )

    # schema_version: absent → v1; integer → as-is.
    raw_schema = forge.get("schema_version")
    schema_version = int(raw_schema) if isinstance(raw_schema, int) else 1

    version = str(forge.get("version", "0.0.0+unknown"))
    project_name = str(forge.get("project_name", ""))
    raw_platform = forge.get("platform_template")
    platform_template = str(raw_platform) if raw_platform else None

    templates_tbl = forge.get("templates") or {}
    templates: dict[str, str] = {k: str(v) for k, v in dict(templates_tbl).items()}

    template_versions_tbl = forge.get("template_versions") or {}
    template_versions: dict[str, str] = {k: str(v) for k, v in dict(template_versions_tbl).items()}

    options_tbl = forge.get("options") or {}
    options: dict[str, Any] = _coerce_options(dict(options_tbl))

    # option_origins: parallel-keyed to options. The exact source of
    # truth depends on schema_version:
    #
    # * v1/v2: no [forge.option_origins] table existed. We can't
    #   recover the user's intent — treat every persisted option as
    #   "default" so the Stage-B resolver tweak silently skips
    #   fragments whose backends aren't present (instead of erroring).
    #   The next generate/update re-stamps the manifest to v3 with
    #   accurate origins for whatever the user actually re-sets.
    # * v3+: read directly from [forge.option_origins]. Tolerate
    #   partial writes — keys present in options but missing from
    #   option_origins fall back to "default" so a hand-edited
    #   manifest doesn't blow up the loader.
    if schema_version < 3:
        option_origins: dict[str, str] = dict.fromkeys(options.keys(), "default")
    else:
        raw_origins = forge.get("option_origins") or {}
        coerced_origins = {str(k): str(_unwrap(v)) for k, v in dict(raw_origins).items()}
        option_origins = {path: coerced_origins.get(path, "default") for path in options}

    provenance_tbl = forge.get("provenance") or {}
    provenance: dict[str, dict[str, Any]] = {}
    for rel_path, entry in dict(provenance_tbl).items():
        if not isinstance(entry, dict):
            continue
        provenance[str(rel_path)] = _coerce_entry(dict(entry))

    merge_blocks_tbl = forge.get("merge_blocks") or {}
    merge_blocks: dict[str, dict[str, Any]] = {}
    for key, entry in dict(merge_blocks_tbl).items():
        if not isinstance(entry, dict):
            continue
        merge_blocks[str(key)] = _coerce_entry(dict(entry))

    # frontend: v4+ persists explicitly under ``[forge.frontend]``.
    # When the table is present + populated, take it at face value
    # (the generator / updater is the source of truth). When the
    # table is missing or carries an empty framework, fall back to
    # the same on-disk inference used for v1 / v2 / v3 manifests —
    # this covers two cases: (a) older manifests pre-dating v4 and
    # (b) v4 writers (e.g. ``--reapply-baseline``) that don't track
    # frontend layer metadata and pass ``frontend=None`` through to
    # ``write_forge_toml``. Reconstruction is best-effort: empty
    # strings mean "no frontend known", which downstream consumers
    # treat as "frontend layer absent".
    frontend_tbl: dict[str, Any] = {}
    if schema_version >= 4:
        raw_frontend = forge.get("frontend") or {}
        frontend_tbl = dict(raw_frontend) if isinstance(raw_frontend, dict) else {}
    explicit_framework = str(_unwrap(frontend_tbl.get("framework", "")))
    if explicit_framework:
        frontend = ForgeFrontendData(
            framework=explicit_framework,
            app_dir=str(_unwrap(frontend_tbl.get("app_dir", ""))),
            layout=str(_unwrap(frontend_tbl.get("layout", ""))),
            ui_kit=str(_unwrap(frontend_tbl.get("ui_kit", ""))),
        )
    else:
        frontend = _infer_frontend_from_v3(path.parent, templates)

    return ForgeTomlData(
        version=version,
        project_name=project_name,
        schema_version=schema_version,
        platform_template=platform_template,
        templates=templates,
        template_versions=template_versions,
        options=options,
        option_origins=option_origins,
        provenance=provenance,
        merge_blocks=merge_blocks,
        frontend=frontend,
    )


# Frontend-framework slugs we know how to dispatch in update phases.
# Mirrors ``forge.generator.TEMPLATE_DIRS`` (built-in frameworks) plus
# the value of ``FrontendFramework.NONE`` for explicit "no frontend".
# Plugin frontends register via ``forge.config.FRONTEND_SPECS`` (consulted
# below in the inference fallback) — listing them here keeps the read
# path stable when a plugin isn't installed.
_BUILTIN_FRONTEND_FRAMEWORKS: tuple[str, ...] = ("vue", "svelte", "flutter")


def _infer_frontend_from_v3(
    project_root: Path,
    templates: dict[str, str],
) -> ForgeFrontendData:
    """Best-effort frontend inference for schema v1 / v2 / v3 manifests.

    Strategy, most-trusted source first:

    1. Walk ``[forge.templates]`` for a key matching a known frontend
       framework. The generator stamps both backend + frontend template
       dirs there; if Vue / Svelte / Flutter shows up, that's the
       frontend.
    2. Otherwise, scan ``apps/`` for a directory containing a recognisable
       marker (``package.json`` + framework dep, ``pubspec.yaml``).
       Used when the manifest is sparse (an older WS2 project, or one
       hand-edited at some point).
    3. Returns the empty record when neither yields a match. The
       updater treats that as "no frontend known" — same effect as
       ``frontend.framework == "none"`` on v4.

    ``project_root`` is the manifest's containing directory (the
    project root). ``templates`` is the parsed ``[forge.templates]``
    map as returned by :func:`read_forge_toml`.

    Pure / no I/O when ``project_root`` doesn't exist on disk (test
    fixtures that fabricate a manifest in a tmpdir without the rest
    of the project layout); falls through to step 3 in that case.
    """
    # Step 1: templates-table inference. Lazy import keeps the
    # manifest module free of the FRONTEND_SPECS dependency at import
    # time (plugin registration runs after manifest is in memory).
    try:
        from forge.config import FRONTEND_SPECS  # noqa: PLC0415

        plugin_frontends = tuple(FRONTEND_SPECS)
    except Exception:  # noqa: BLE001
        plugin_frontends = ()
    known = set(_BUILTIN_FRONTEND_FRAMEWORKS) | set(plugin_frontends)

    for key in templates:
        if key in known:
            # Assume the conventional ``apps/frontend/`` slot; the
            # generator hardcodes ``frontend_slug = "frontend"``. The
            # on-disk presence check below is opportunistic — the
            # updater treats a missing app_dir as "no frontend on
            # disk", which matches what the user actually has.
            app_dir = "apps/frontend"
            if not (project_root / app_dir).is_dir():
                # Fall back to scanning apps/ for any directory that
                # exists (the plugin frontends sometimes ship to
                # apps/<plugin_slug>/).
                scanned = _scan_apps_for_frontend(project_root)
                if scanned:
                    app_dir = scanned
            return ForgeFrontendData(framework=key, app_dir=app_dir)

    # Step 2: on-disk inference. The framework is derived from the
    # marker file we find — ``package.json`` plus a recognisable
    # dependency name, or ``pubspec.yaml`` for Flutter. Walk every
    # ``apps/<sub>/`` (not just the first with a marker file) so a
    # project with sibling apps like ``apps/admin/`` (React) + ``apps/
    # frontend/`` (Vue) still resolves to the recognised framework
    # rather than collapsing to the first lexicographic entry.
    apps_dir = project_root / "apps"
    if apps_dir.is_dir():
        # Prefer the conventional ``apps/frontend/`` slot if it carries
        # a recognised marker; otherwise scan siblings in lexicographic
        # order. The conventional path matches what the generator
        # emits today and avoids ambiguity for the common case.
        candidates: list[Path] = []
        conventional = apps_dir / "frontend"
        if conventional.is_dir():
            candidates.append(conventional)
        for sub in sorted(apps_dir.iterdir()):
            if sub == conventional or not sub.is_dir():
                continue
            candidates.append(sub)
        for sub in candidates:
            framework = _framework_from_app_dir(sub)
            if framework:
                return ForgeFrontendData(
                    framework=framework,
                    app_dir=f"apps/{sub.name}",
                )

    return ForgeFrontendData()


def _scan_apps_for_frontend(project_root: Path) -> str:
    """Return the POSIX rel-path of the first ``apps/<sub>/`` dir with a marker.

    Recognised markers: ``package.json`` (Vue / Svelte / plugin JS
    frameworks), ``pubspec.yaml`` (Flutter). Returns ``""`` when
    ``apps/`` is missing or has no marker-bearing sub-directory.

    Step 1 of :func:`_infer_frontend_from_v3` uses this for the
    templates-table fallback (when ``[forge.templates]`` lists a
    frontend framework but the conventional ``apps/frontend/`` slot
    isn't on disk — the project may have used a plugin slug).
    """
    apps = project_root / "apps"
    if not apps.is_dir():
        return ""
    for sub in sorted(apps.iterdir()):
        if not sub.is_dir():
            continue
        if (sub / "package.json").is_file() or (sub / "pubspec.yaml").is_file():
            # POSIX rel-path. Tests run on Linux/macOS; even on Windows
            # the manifest is canonically forward-slashed so consumers
            # don't have to normalise.
            return f"apps/{sub.name}"
    return ""


def _framework_from_app_dir(app_dir: Path) -> str:
    """Heuristic framework detection from a frontend app directory.

    Reads ``package.json`` once and scans for a recognisable
    dependency (``vue`` / ``svelte``). Falls back to Flutter when
    ``pubspec.yaml`` is present. Returns ``""`` on no match — the
    caller treats that as "frontend layer absent from manifest" and
    skips frontend phases (safer than guessing wrong and applying
    Vue fragments to a Svelte tree).
    """
    pkg = app_dir / "package.json"
    if pkg.is_file():
        try:
            import json  # noqa: PLC0415

            payload = json.loads(pkg.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            payload = {}
        deps: dict[str, Any] = {}
        for key in ("dependencies", "devDependencies"):
            section = payload.get(key) or {}
            if isinstance(section, dict):
                deps.update(section)
        if "svelte" in deps or "@sveltejs/kit" in deps:
            return "svelte"
        if "vue" in deps:
            return "vue"
    if (app_dir / "pubspec.yaml").is_file():
        return "flutter"
    return ""


def _coerce_options(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize the ``[forge.options]`` table into a plain dict.

    tomlkit returns its own wrappers (``Bool``, ``Integer``, ``String``,
    ``Array``); unwrap to native Python so downstream comparisons work.
    """
    out: dict[str, Any] = {}
    for key, value in raw.items():
        out[str(key)] = _unwrap(value)
    return out


def _coerce_entry(raw: dict[str, Any]) -> dict[str, Any]:
    """Unwrap tomlkit values inside one provenance / merge_block entry.

    Keeps the dict-of-Any shape so callers see native Python types
    (str, int, list[int] for ``line_range``).
    """
    return {str(k): _unwrap(v) for k, v in raw.items()}


def _unwrap(value: Any) -> Any:
    """Convert a tomlkit value to its native Python equivalent."""
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    if isinstance(value, str):
        return str(value)
    if isinstance(value, list | tuple):
        return [_unwrap(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _unwrap(v) for k, v in value.items()}
    return value


def write_forge_toml(
    path: Path,
    *,
    version: str,
    project_name: str,
    templates: dict[str, str],
    options: dict[str, Any],
    option_origins: dict[str, str] | None = None,
    provenance: dict[str, dict[str, Any]] | None = None,
    merge_blocks: dict[str, dict[str, Any]] | None = None,
    template_versions: dict[str, str] | None = None,
    frontend: ForgeFrontendData | None = None,
    platform_template: str | None = None,
    schema_version: int = CURRENT_SCHEMA_VERSION,
) -> None:
    """Emit ``forge.toml`` with all v4 sub-tables.

    ``schema_version`` defaults to the current version; existing callers
    that omit it produce a v4 manifest. ``template_versions`` may be
    None or empty when the caller doesn't know per-template versions
    (legacy migration paths) — the table is omitted in that case.

    ``option_origins`` is the v3 provenance table: dotted option path
    → ``"user"`` / ``"default"``. When ``None`` (existing call sites
    that haven't been ported yet — generator, updater, resolver,
    reapply_baseline, accept), every entry in ``options`` is recorded
    as ``"user"`` to preserve current behavior (the resolver in Stage B
    will treat empty/missing origins as user-set, matching this
    fallback). Origins for paths absent from ``options`` are silently
    dropped — the two tables must stay parallel-keyed.

    ``{}`` (empty dict) and ``None`` produce identical output today:
    both result in every option being recorded as ``"user"`` because
    the per-key fallback at the merge step defaults to ``"user"`` for
    any key not in ``option_origins``. Stage B callers that need to
    express "all defaulted" must pass a fully-populated dict with
    explicit ``"default"`` values; ``{}`` is not a shortcut.

    ``merge_blocks`` stores per-block metadata used by the three-way
    merge runtime (see :mod:`forge.sync.merge`) and by the
    reverse-direction harvester (see :mod:`forge.sync.project_to_forge`
    — Phase 4).

    ``frontend`` is the v4 ``[forge.frontend]`` table — framework name
    + on-disk app dir. ``None`` or a record with empty fields omits
    the table entirely; existing call sites (``--reapply-baseline``,
    ``--accept-harvested``, ``--remove-fragment``) that don't track
    the frontend separately pass ``None`` and the read path
    re-infers from disk on the next load.
    """
    doc = tomlkit.document()
    doc.add(tomlkit.comment("Generated by forge — do not edit by hand."))
    doc.add(
        tomlkit.comment(
            "Re-render any subdirectory with `copier update` using its `.copier-answers.yml`."
        )
    )

    forge_tbl = tomlkit.table()
    forge_tbl.add("schema_version", schema_version)
    forge_tbl.add("version", version)
    forge_tbl.add("project_name", project_name)
    # Phase 4 ``--platform`` preset — emitted only when set, so no-platform
    # projects keep a byte-identical [forge] table.
    if platform_template:
        forge_tbl.add("platform_template", platform_template)

    tpl_tbl = tomlkit.table()
    for key in sorted(templates):
        tpl_tbl.add(key, templates[key])
    forge_tbl.add("templates", tpl_tbl)

    if template_versions:
        tv_tbl = tomlkit.table()
        for key in sorted(template_versions):
            tv_tbl.add(key, template_versions[key])
        forge_tbl.add("template_versions", tv_tbl)

    # v4: [forge.frontend] table. Emit only when the caller passed
    # explicit frontend metadata with a non-empty framework — projects
    # without a frontend (backend-only) omit the table entirely. The
    # read path treats both "table absent" and "framework == none" as
    # "no frontend present", so existing v3-writing call sites
    # (--reapply-baseline, --accept-harvested, --remove-fragment) that
    # pass ``frontend=None`` still produce a manifest the read path
    # can reconstruct from disk on the next load.
    if frontend is not None and frontend.framework:
        fe_tbl = tomlkit.table()
        fe_tbl.add("framework", frontend.framework)
        if frontend.app_dir:
            fe_tbl.add("app_dir", frontend.app_dir)
        if frontend.layout:
            fe_tbl.add("layout", frontend.layout)
        if frontend.ui_kit:
            fe_tbl.add("ui_kit", frontend.ui_kit)
        forge_tbl.add("frontend", fe_tbl)

    options_tbl = tomlkit.table()
    for key in sorted(options):
        options_tbl.add(key, options[key])
    forge_tbl.add("options", options_tbl)

    # Backwards compat: callers that don't yet pass `option_origins`
    # (generator, updater, resolver, reapply_baseline, accept,
    # remove_fragment) get every option stamped as "user" — preserving
    # the pre-WS2 behavior where every persisted value was treated as
    # an explicit user choice. Stage B will update those call sites
    # to pass real origins.
    effective_origins: dict[str, str]
    if option_origins is None:
        effective_origins = dict.fromkeys(options.keys(), "user")
    else:
        # Drop origins for paths not in `options` (the two tables MUST
        # stay parallel-keyed — Stage B's resolver relies on the
        # invariant). Missing origins for present options fall back to
        # "user", consistent with the None-arg path above.
        effective_origins = {key: option_origins.get(key, "user") for key in options}
    origins_tbl = tomlkit.table()
    for key in sorted(effective_origins):
        origins_tbl.add(key, effective_origins[key])
    forge_tbl.add("option_origins", origins_tbl)

    if provenance:
        prov_tbl = tomlkit.table()
        for rel_path in sorted(provenance):
            entry = provenance[rel_path]
            sub = tomlkit.table()
            for k in sorted(entry):
                sub.add(k, entry[k])
            prov_tbl.add(rel_path, sub)
        forge_tbl.add("provenance", prov_tbl)

    if merge_blocks:
        mb_tbl = tomlkit.table()
        for key in sorted(merge_blocks):
            entry = merge_blocks[key]
            sub = tomlkit.table()
            for k in sorted(entry):
                sub.add(k, entry[k])
            mb_tbl.add(key, sub)
        forge_tbl.add("merge_blocks", mb_tbl)

    doc.add("forge", forge_tbl)
    path.write_text(tomlkit.dumps(doc), encoding="utf-8", newline="\n")
