# Workload recommendations

```yaml
project_name: example
existing_stack: python
team_languages: [python, node]
services:
  - name: processor
    workload: processing
    cpu_intensive: true
    memory_mb: 512
  - name: intelligence
    workload: ai
    options:
      rag.backend: qdrant
  - name: notifications
    workload: notifications
```

Supported workloads: `processing`, `ai`, `notifications`, `crud`, `llm_api`.
Each service can specify `language`, `latency_ms`, `features`, `options`, and
`required_library_languages`. Explicit language choices are retained or
rejected with a capability explanation; they are never silently replaced.

The deterministic policy prefers Rust for CPU-intensive processing, Python
for AI/ML/RAG, and Node/TypeScript for communication-heavy I/O. Go is a
candidate for every workload but only wins through the existing stack, team
languages or an explicit choice. Existing runtime investments and team
expertise affect the score. Library and Forge capability requirements are
hard constraints, evaluated for each service.

Calling a remote LLM does not itself require Python. CPU-bound JavaScript can
use worker pools; Python can delegate numeric work to native/GPU libraries.
Benchmark latency, throughput and memory before splitting an existing service
solely for performance. Minimize unnecessary runtimes and service boundaries.

Notification delivery requires durable queues, retry limits, idempotency,
rate limits and dead-letter handling independently of the implementation
language. Inspect `queue.backend` support before recommending an adapter.

The result includes reasons, alternatives, rejected languages and assumptions.
Distinct option sets produce separate service configs because Forge's current
configuration model has project-wide options. Never combine those configs by
silently dropping or broadening service requirements.

The preferences are policy choices, not performance guarantees. Consult the
[Node event-loop guidance](https://nodejs.org/en/learn/asynchronous-work/dont-block-the-event-loop),
[Rust concurrency guide](https://doc.rust-lang.org/book/ch16-00-concurrency.html),
and [Python multiprocessing documentation](https://docs.python.org/3/library/multiprocessing.html)
when deciding how to isolate CPU work and bound concurrency. Choose libraries
and deployment constraints first, then benchmark representative workloads.
