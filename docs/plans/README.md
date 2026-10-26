# Decisions, proposals, and implementation history

Current behavior belongs in [architecture](../architecture/README.md),
[guides](../guides/README.md), and [operations](../operations/README.md).
This index preserves the reasoning and implementation evidence behind it.

| Record | Location | How to use it |
| --- | --- | --- |
| Architecture decision | [Numbered ADRs](../architecture-decisions/README.md) | Read the context, chosen tradeoff, and consequences. |
| Proposal or contract | [Numbered RFCs](../rfcs/README.md) | Check the document's status; proposed/deferred work is not a shipped capability. |
| Implementation plan or review | [Dated plans](../../plans/) | Historical scope, sequencing, findings, and validation at the time. |
| User-visible changes | [Changelog](../../CHANGELOG.md) | Released/unreleased behavior and compatibility notes. |
| Migration procedure | [Upgrade guide](../../UPGRADING.md) | Actions needed when moving between versions. |

## Plans by subject

| Subject | Implementation evidence | Current guide |
| --- | --- | --- |
| Generated ownership, quality gates, recommendations, agent skill | [October 2026 implementation plan](../../plans/2026-10-07-generated-quality.md) | [Generated-code quality](../operations/generated-code-quality.md) |
| Vue UI kit option (Nuxt UI) and the content-security repair | [Nuxt UI kit plan](../../plans/2026-10-22-vue-ui-kit-nuxt-ui.md), accepted | [UI kits](../guides/frontend-layouts.md#ui-kits), [Nuxt UI projects](../guides/agentic-usage.md#nuxt-ui-projects), [ADR-011](../architecture-decisions/ADR-011-ui-kit-option-for-the-vue-template.md) |
| Several frontends per project | [Multi-frontend plan](../../plans/2026-10-25-multi-frontend.md), implemented | [Getting started](../guides/getting-started.md), [ADR-012](../architecture-decisions/ADR-012-a-list-of-frontends.md) |
| Layered UI components | [Component model plan](../../plans/2026-06-02-layered-component-model-plan.md) | [Generator architecture](../architecture/generator.md), [ADR-010](../architecture-decisions/ADR-010-layered-component-model.md) |
| Multi-service generation | [Platform backport plan](../../plans/2026-06-08-platform-backport-plan.md) | [Platform presets](../guides/platforms.md) |
| Tenant claim isolation | [Token claim multitenancy plan](../../plans/2026-06-09-token-claim-multitenancy.md) | [Platform overview](../architecture/overview.md#tenant-data-isolation) |
| Deployment topology | [Helm plan](../../plans/2026-06-14-topology-aware-helm-plan.md), [implementation review](../../plans/2026-06-14-topology-aware-helm-impl-review-waves234.md) | [Deployment](../operations/deployment.md) |
| CI and audit work | [CI health plan](../../plans/2026-05-19-forge-ci-health-plan.md), [audit report](../../plans/2026-06-17-audit-remediation-report.md) | [Validation matrix](../operations/validation-matrix.md) |
| Ports, schemas, MCP, canvas, and fragment pipelines | [May 2026 implementation reviews](../../plans/) | [Architecture](../architecture/README.md), [MCP](../reference/mcp.md) |

Retain dated filenames in the root `plans/` directory so existing issue and PR
references continue to resolve. New records should state their date, status,
source revision or PR, and links to current documentation when work completes.
Do not copy every plan into `docs/`; this directory is the navigation bridge.

[Documentation home](../README.md)
