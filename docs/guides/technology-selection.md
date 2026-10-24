# Select technologies from service requirements

Start with the service's behavior, dependencies, operational constraints, and
team. Forge's recommender applies a deterministic policy against its installed
capability registry; it does not benchmark the service or call an LLM.

## Selection policy

| Workload | Initial preference | What can change the decision |
| --- | --- | --- |
| CPU-heavy processing, parsing, transformation | Rust/Axum | Required libraries, development cost, existing service boundaries, representative benchmarks |
| Local AI/ML, inference orchestration, RAG | Python/FastAPI | Native/GPU library availability, model serving architecture, Forge adapter support |
| Communication and notifications, concurrent network work | Node/TypeScript with Fastify | Existing runtime, queue/provider support, CPU work that must leave the event loop |
| CRUD | Existing backend language, otherwise Python | Team skills, persistence libraries, compatible Forge options |
| Calling a remote LLM API | Existing backend language, otherwise Python | Provider SDK and Forge adapter support; remote API calls alone do not require Python |

These are policy starting points, not universal performance rankings. Python can
perform numerical work in native/GPU libraries; Node can move CPU work to worker
threads or another service; Rust still needs appropriate concurrency limits and
I/O design. Measure latency, throughput, memory, and operational cost before
splitting a service solely for performance.

The implementation gives the preferred workload language 4 points, an existing
runtime 3 points, a team language 2 points, and Rust another 3 points for
`cpu_intensive: true`. Go is scored like every other candidate but is never a
workload's default; it is selected through `existing_stack`, `team_languages`,
or an explicit `language`. Library and capability constraints eliminate candidates
before selection. An explicit compatible `language` wins regardless of score;
an incompatible explicit choice fails with an explanation. `latency_ms` and
`memory_mb` are recorded as benchmark assumptions, not guaranteed capacities.

## Supply requirements

```yaml
# requirements.yaml
project_name: commerce
existing_stack: node
team_languages: [node, python]
services:
  - name: processor
    workload: processing
    cpu_intensive: true
    memory_mb: 512
    required_library_languages: [rust]
  - name: intelligence
    workload: ai
    options:
      rag.backend: qdrant
  - name: notifications
    workload: notifications
  - name: assistant-api
    workload: llm_api
    language: node
    options:
      llm.provider: openai
```

```bash
forge --recommend requirements.yaml --json > recommendation.json
```

Each service accepts `name`, `workload`, optional `language`, `cpu_intensive`,
`memory_mb`, `latency_ms`, `required_library_languages`, `features`, and `options`.
Supported workloads are `processing`, `ai`, `notifications`, `crud`, and `llm_api`.
Names must be valid unique slugs. `features` is the CRUD entity list (default
`[items]`); `options` selects registered platform capabilities.

The result includes `decisions`, ranked `alternatives`, rejected languages with
reasons, assumptions, and ready-to-review configuration:

- `config` is a combined headless project only when every service has the same
  option map.
- `service_configs` contains separate configs when requirements differ.
  Forge options are project-wide; it does not support arbitrary per-service
  option overrides in one project.

With the example above, differing option maps mean `config` is null. Save each
selected `service_configs` entry to its own YAML/JSON file, inspect it, and plan
it separately. Do not merge them by silently dropping service requirements.
Separate generated projects also need explicit deployment/auth integration;
the recommender does not synthesize that integration for you.

```bash
forge --config intelligence.yaml --plan --json
forge --config intelligence.yaml --yes --no-docker --json
```

`intelligence.yaml` here is the selected entry saved from the result. Check the
[option catalog](../FEATURES.md) and [support limits](../reference/limitations.md)
when a language is rejected. A third-party library existing for a language does
not mean Forge ships its adapter.

## Boundaries and reliability

Use the fewest runtimes that satisfy real requirements. Group tightly coupled
work behind one service until independent scaling, ownership, failure isolation,
or library constraints justify another. The four [platform presets](platforms.md)
provide topology starting points; the recommender chooses languages, not an
optimal distributed-system design.

Notification delivery needs durable queues, idempotency, bounded retries,
provider rate limits, and failure/dead-letter handling in every language. Check
`queue.backend` and its adapter semantics. Selecting Node or enabling a queue
option does not implement the application's delivery guarantees.

The canonical [skill workload reference](../../forge/templates/_common/skills/forge-platform/references/workloads.md)
shares these rules with coding agents. For concurrency design, see the primary
[Node event-loop guidance](https://nodejs.org/en/learn/asynchronous-work/dont-block-the-event-loop),
[Rust concurrency guide](https://doc.rust-lang.org/book/ch16-00-concurrency.html),
and [Python multiprocessing reference](https://docs.python.org/3/library/multiprocessing.html).
