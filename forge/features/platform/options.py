"""``platform.*`` options — admin UI, webhooks, CLI extensions, MCP, AGENTS.md."""

from __future__ import annotations

from forge.api import ForgeAPI
from forge.options._registry import (
    FeatureCategory,
    Option,
    OptionType,
)


def register_all(api: ForgeAPI) -> None:
    api.add_option(
        Option(
            path="platform.admin",
            type=OptionType.BOOL,
            default=False,
            summary="SQLAdmin UI at /admin -- tenant-scoped ModelViews.",
            description="""\
A browser-facing admin UI mounted at /admin, built on SQLAdmin. It
auto-registers ModelViews for whichever tables the enabled options
have shipped — items, audit_logs, conversations, messages, webhooks
— and skips any model whose Python import fails.

BACKENDS: python
ENDPOINTS: /admin (HTML UI)
REQUIRES: ADMIN_PANEL_MODE=disabled|dev|all (env var); sqladmin +
itsdangerous.""",
            category=FeatureCategory.PLATFORM,
            stability="beta",
            # Initiative #7 — SQLAdmin renders model views over the DB.
            requires_database=True,
            enables={True: ("admin_panel",)},
        )
    )

    api.add_option(
        Option(
            path="platform.webhooks",
            type=OptionType.BOOL,
            default=False,
            summary="Outbound registry + HMAC-signed delivery (ts + nonce + body).",
            description="""\
A registry + HMAC-SHA256 signed outbound delivery pipeline. Clients
POST to /api/v1/webhooks to register a target URL; your code calls
``fireEvent`` to deliver a signed JSON payload. Receiver verifies the
same way across every backend — the signature header format is
identical.

BACKENDS: python, node, rust, go
ENDPOINTS: /api/v1/webhooks (CRUD + /{id}/test fire)""",
            category=FeatureCategory.PLATFORM,
            stability="beta",
            # Initiative #7 — webhook registry persists target URLs + delivery
            # attempts to the DB.
            requires_database=True,
            enables={True: ("webhooks",)},
        )
    )

    api.add_option(
        Option(
            path="platform.cli_extensions",
            type=OptionType.BOOL,
            default=False,
            summary="Typer subcommands -- `app info`, `app tools`, `app rag`.",
            description="""\
Extends the generated service's ``app`` typer CLI with operational
subcommands: ``app info show`` (environment dump), ``app tools
list``/``invoke`` (exercise registered agent tools), ``app rag
ingest`` (ingest a local file into the knowledge base). Each subcommand
degrades gracefully — if its prerequisite option isn't enabled, it
prints a hint and exits non-zero.

BACKENDS: python
ENDPOINTS: none — CLI surface only.""",
            category=FeatureCategory.PLATFORM,
            stability="beta",
            enables={True: ("cli_commands",)},
        )
    )

    api.add_option(
        Option(
            path="platform.mcp",
            type=OptionType.BOOL,
            default=False,
            summary="Model Context Protocol router + UI scaffolds for tool discovery and approval.",
            description="""\
Scaffolds a backend ``/mcp/tools`` + ``/mcp/invoke`` router (Python,
FastAPI) plus Vue ToolRegistry + ApprovalDialog components. Config
lives at project-root ``mcp.config.json`` (schema at
``forge/templates/_shared/mcp/mcp_config_schema.json``). Real MCP
subprocess spawning and tool-call proxying land in 1.0.0a3 — this alpha
ships the stable endpoints + UI surface so integrators can start
wiring today.

BACKENDS: python
FRONTENDS: vue (svelte + flutter in 1.0.0a3)
DOCS: docs/mcp.md.""",
            category=FeatureCategory.CONVERSATIONAL_AI,
            enables={True: ("mcp_server", "mcp_ui")},
        )
    )

    api.add_option(
        Option(
            path="platform.agents_md",
            type=OptionType.BOOL,
            default=True,
            summary="Drops AGENTS.md + CLAUDE.md for AI-coding-agent orientation.",
            description="""\
Drops AGENTS.md + CLAUDE.md at the project root so AI coding agents
(Claude Code, Cursor, Copilot workspaces) have a structured
orientation document before they touch generated code. Covers the
option stamp, backend layout, test commands, and the house
conventions so agents ship PRs that match the project's style on the
first try.

BACKENDS: python, node, rust, go (same content, project-scoped)""",
            category=FeatureCategory.PLATFORM,
            enables={True: ("agents_md",)},
        )
    )

    # ── Phase 4: multi-service platform synthesis ──────────────────────────
    # Inert knobs until the synthesis pass consumes them (later Phase 4
    # sub-steps). Defaults keep single-service + existing multi-backend output
    # byte-identical; both are no-ops at their default value.
    api.add_option(
        Option(
            path="auth.service_discovery",
            type=OptionType.BOOL,
            default=False,
            summary="Synthesize an S2S client registry + inter-service URLs across backends.",
            description="""\
Multi-service platform synthesis. When ON (and the project has >1 backend),
forge computes a service-to-service auth graph from each backend's
``depends_on`` and emits: a gatekeeper ``service_registry.yaml`` (per-service
client id / secret / audiences / scopes), per-backend S2S credentials +
``INTERNAL_SERVICE_URL_*`` env vars, and the matching realm clients — so the
generated services can authenticate to each other out of the box.

OFF (default): no synthesis runs; single-service and existing multi-backend
output is byte-identical.

BACKENDS: python (tier-1); node/rust S2S callers follow.
REQUIRES: >1 backend; the gatekeeper auth provider for S2S credentials.""",
            category=FeatureCategory.PLATFORM,
            stability="beta",
            requires_backend=True,
        )
    )

    api.add_option(
        Option(
            path="infrastructure.event_bus",
            type=OptionType.ENUM,
            default="none",
            options=("none", "postgres_notify"),
            summary="Cross-service async event bus.",
            description="""\
Cross-service asynchronous eventing. ``none`` (default) ships no event bus —
byte-identical to today. ``postgres_notify`` provisions a shared ``events``
database + a Postgres LISTEN/NOTIFY transactional-outbox bus URL injected into
every backend, so services can publish/subscribe domain events.

BACKENDS: python (tier-1).
REQUIRES: a database (postgres).""",
            category=FeatureCategory.PLATFORM,
            stability="beta",
            requires_database=True,
        )
    )
