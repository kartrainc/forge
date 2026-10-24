"""``async.*`` and ``queue.*`` — off-thread job processing."""

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
            path="async.task_queue",
            type=OptionType.BOOL,
            default=False,
            summary="Redis-backed job queue (Taskiq / BullMQ / Apalis / asynq).",
            description="""\
A Redis-backed job queue + example task + worker binary. Define jobs as
regular async functions, enqueue them from request handlers, process
them out-of-process in a dedicated worker container. Ships with Taskiq
(Python), BullMQ + ioredis (Node), Apalis (Rust) and asynq (Go, with a
``cmd/worker`` binary) — four ecosystems with the same env-var convention
(TASKIQ_BROKER_URL).

BACKENDS: python, node, rust, go
REQUIRES: TASKIQ_BROKER_URL → Redis.""",
            category=FeatureCategory.ASYNC_WORK,
            stability="beta",
            enables={True: ("background_tasks",)},
        )
    )

    api.add_option(
        Option(
            path="async.rag_ingest_queue",
            type=OptionType.BOOL,
            default=False,
            summary="Taskiq tasks that move RAG ingest off the request thread.",
            description="""\
Taskiq tasks that move RAG ingestion off the request thread. Enqueue
with ``await ingest_text_task.kiq(...)`` or
``ingest_pdf_bytes_task.kiq(...)`` from any handler — the worker picks
it up and runs chunk + embed + store in the background. The endpoint
returns immediately with a task ID.

BACKENDS: python
REQUIRES: rag.backend ≠ none + async.task_queue = true.""",
            category=FeatureCategory.ASYNC_WORK,
            stability="experimental",
            enables={True: ("rag_sync_tasks",)},
            # rag_sync_tasks depends on rag_pipeline -> conversation_persistence
            # (DB-backed). Init #7 follow-up: codex flagged this gap.
            requires_database=True,
        )
    )

    api.add_option(
        Option(
            path="queue.backend",
            type=OptionType.ENUM,
            default="none",
            options=("none", "redis", "sqs", "bullmq", "apalis", "asynq"),
            summary="Background-work queue — selects the QueuePort adapter (per RFC-012).",
            description="""\
Selects which queue implementation the ``QueuePort`` resolves to.
Each value is scoped to the backend language whose adapter ecosystem
it belongs to — see docs/rfcs/RFC-012-forgequeue-port.md for the
per-language mapping:

- ``redis`` / ``sqs``: Python adapters (Taskiq broker, AWS SQS).
- ``bullmq``: Node adapter (BullMQ + ioredis).
- ``apalis``: Rust adapter (Apalis + Redis).
- ``asynq``: Go adapter (asynq + Redis).

In a polyglot project, the resolver targets the adapter only at the
backend language whose ecosystem it belongs to. Other backends in
the same project receive the port (typing only, no concrete adapter)
unless paired with their own queue.backend value via per-language
overrides (future work; see RFC-012 §"Drawbacks").

OPTIONS: none | redis | sqs | bullmq | apalis | asynq
BACKENDS: python (redis, sqs), node (bullmq), rust (apalis), go (asynq)
DEPENDENCY: redis-py (redis), aioboto3 (sqs), bullmq+ioredis
    (bullmq), apalis+apalis-redis (apalis), hibiken/asynq (asynq)
ENV: REDIS_URL / AWS_REGION / TASKIQ_BROKER_URL""",
            category=FeatureCategory.ASYNC_WORK,
            enables={
                "redis": ("queue_port", "queue_redis"),
                "sqs": ("queue_port", "queue_sqs"),
                "bullmq": ("queue_port", "queue_bullmq"),
                "apalis": ("queue_port", "queue_apalis"),
                "asynq": ("queue_port", "queue_asynq"),
            },
        )
    )
