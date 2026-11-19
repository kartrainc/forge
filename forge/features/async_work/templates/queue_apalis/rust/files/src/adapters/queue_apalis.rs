//! Apalis queue adapter — concrete ``QueuePort`` impl backed by
//! Apalis + Redis.
//!
//! Apalis's native model is one storage per job type; the ForgeQueue
//! port (RFC-012) abstracts over a per-topic, untyped-JSON body. The
//! adapter bridges by wrapping every message in a generic
//! ``JsonEnvelope`` and using a per-topic ``RedisStorage``, cached on
//! the adapter struct.
//!
//! Consumption: apalis has no public "pull one message" call —
//! ``RedisStorage::fetch_next`` is private and worker-scoped. So
//! ``consume`` runs a real apalis worker (the public ``WorkerBuilder``
//! model) whose handler hands each decoded job to the port's stream and
//! then BLOCKS on a per-message disposition the consumer signals via
//! ``ack`` / ``nack``. That keeps apalis the source of truth for the
//! job lifecycle: the handler returns ``Ok`` (ack), an error
//! (requeue/retry) or an ``AbortError`` (dead-letter) according to the
//! consumer's decision, so delivery is genuinely at-least-once and
//! ``nack`` actually requeues / DLQs rather than just dropping bookkeeping.
//!
//! The worker future is driven INLINE inside the consume stream (not a
//! detached ``tokio::spawn``), so dropping the stream stops the worker —
//! no leaked background task, and an un-acked in-flight message is
//! redelivered rather than silently marked done.
//!
//! The ``topic`` parameter is the Apalis queue; each unique topic
//! gets its own ``RedisStorage`` instance, lazy-initialized on first use.

use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

use apalis::prelude::*;
use apalis_redis::{Config as RedisConfig, RedisStorage};
use async_trait::async_trait;
use futures::stream::BoxStream;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tokio::sync::{Mutex, mpsc, oneshot};

use crate::ports::queue::{QueueError, QueueMessage, QueuePort};

/// What the consumer decided about an in-flight message. The apalis
/// worker's handler awaits one of these per message and maps it onto
/// apalis's job outcome (ack / retry / dead-letter).
enum Disposition {
    Ack,
    Requeue,
    Dlq,
}

/// How often an idle worker asks Redis for due jobs (apalis 0.7's default):
/// one fetch per interval for each open ``consume`` stream, per topic and replica.
const POLL_INTERVAL: Duration = Duration::from_millis(100);

fn worker_error(msg: &str) -> BoxDynError {
    msg.into()
}

fn abort_error(msg: &str) -> BoxDynError {
    AbortError::new(msg).into()
}

/// The worker's handler: hands the job and a disposition sender to the
/// consume stream, then maps the consumer's ack/nack onto the job outcome.
async fn hand_over(
    job: JsonEnvelope,
    tx: mpsc::Sender<(JsonEnvelope, oneshot::Sender<Disposition>)>,
) -> Result<(), BoxDynError> {
    let (disp_tx, disp_rx) = oneshot::channel::<Disposition>();
    // A closed channel means the consumer stream was dropped before this
    // job was handed over — fail it so it isn't acked. (If the stream drops
    // AFTER hand-over, ``disp_rx.await`` is cancelled with the worker rather
    // than resolving here; that job is redelivered via apalis's orphan
    // recovery once the worker misses its heartbeats — 60 s by default
    // (``Config::heartbeat_interval`` 30 s × ``missed_heartbeats`` 2) — not
    // immediately.)
    if tx.send((job, disp_tx)).await.is_err() {
        return Err(worker_error("consumer stream closed"));
    }
    match disp_rx.await {
        Ok(Disposition::Ack) => Ok(()),
        Ok(Disposition::Requeue) => Err(worker_error("nack: requeue")),
        Ok(Disposition::Dlq) => Err(abort_error("nack: dead-letter")),
        // Consumer dropped the message without acking → requeue (at-least-once).
        Err(_) => Err(worker_error("consumer dropped message")),
    }
}

/// Apalis-side envelope. Maps to the cross-language ``{id, body}`` shape
/// from RFC-012; ``topic`` is carried inside the message rather than on
/// the storage so we can multiplex topics on a shared Redis connection.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JsonEnvelope {
    pub id: String,
    pub topic: String,
    pub body: Value,
}

fn broker_url() -> String {
    std::env::var("TASKIQ_BROKER_URL").unwrap_or_else(|_| "redis://redis:6379/2".to_string())
}

/// Concrete adapter. Stores per-topic ``RedisStorage<JsonEnvelope>``
/// handles behind a ``Mutex`` so consumers and producers across tokio
/// tasks can share one adapter instance. ``pending`` maps a yielded
/// message's receipt to the oneshot the worker handler is awaiting, so
/// ``ack`` / ``nack`` can resolve the job's outcome.
pub struct ApalisQueueAdapter {
    storages: Arc<Mutex<HashMap<String, RedisStorage<JsonEnvelope>>>>,
    pending: Arc<Mutex<HashMap<String, oneshot::Sender<Disposition>>>>,
}

impl ApalisQueueAdapter {
    pub fn new() -> Self {
        Self {
            storages: Arc::new(Mutex::new(HashMap::new())),
            pending: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    async fn storage_for(&self, topic: &str) -> Result<RedisStorage<JsonEnvelope>, QueueError> {
        let mut map = self.storages.lock().await;
        if let Some(existing) = map.get(topic) {
            return Ok(existing.clone());
        }
        let conn = apalis_redis::connect(broker_url())
            .await
            .map_err(|e| QueueError::Transport(e.to_string()))?;
        // Apalis keys messages per ``Config::queue``; keying it by the
        // topic name keeps separate topics in separate Redis keysets.
        let cfg = RedisConfig::default().queue(topic);
        let storage = RedisStorage::new(conn).with_config(cfg);
        map.insert(topic.to_string(), storage.clone());
        Ok(storage)
    }
}

impl Default for ApalisQueueAdapter {
    fn default() -> Self {
        Self::new()
    }
}

#[async_trait]
impl QueuePort for ApalisQueueAdapter {
    async fn enqueue(
        &self,
        topic: &str,
        body: Value,
        delay_seconds: u64,
    ) -> Result<String, QueueError> {
        let id = uuid::Uuid::new_v4().to_string();
        let envelope = JsonEnvelope {
            id: id.clone(),
            topic: topic.to_string(),
            body,
        };
        let mut storage = self.storage_for(topic).await?;
        if delay_seconds > 0 {
            // Apalis schedules a task through its ``run_at``, set from a
            // delay after "now".
            let task = TaskBuilder::new(envelope)
                .run_after(Duration::from_secs(delay_seconds))
                .build();
            storage
                .push_task(task)
                .await
                .map_err(|e| QueueError::Transport(e.to_string()))?;
        } else {
            storage
                .push(envelope)
                .await
                .map_err(|e| QueueError::Transport(e.to_string()))?;
        }
        Ok(id)
    }

    fn consume<'a>(
        &'a self,
        topic: &'a str,
        batch_size: usize,
    ) -> BoxStream<'a, Result<QueueMessage, QueueError>> {
        let pending = self.pending.clone();
        let stream = async_stream::stream! {
            let storage = match self.storage_for(topic).await {
                Ok(s) => s,
                Err(e) => {
                    yield Err(e);
                    return;
                }
            };
            // The apalis worker's handler hands each job + a disposition
            // sender to this stream over a bounded channel, then awaits the
            // consumer's ack/nack.
            let (tx, mut rx) =
                mpsc::channel::<(JsonEnvelope, oneshot::Sender<Disposition>)>(
                    batch_size.max(1),
                );
            // The worker name is the Redis consumer identity apalis heartbeats
            // and orphan-tracks under, so make it unique per consume() call —
            // a shared name across stream instances or service replicas would
            // confuse orphan recovery for in-flight jobs.
            let worker_name = format!("forge-consumer-{topic}-{}", uuid::Uuid::new_v4());
            // apalis 1.x parks a worker after an empty fetch until something
            // wakes it; the interval picks up jobs pushed from elsewhere and
            // retries and delays falling due, as apalis 0.7's poll did.
            let worker = WorkerBuilder::new(worker_name)
                .backend(storage.poll_with_interval(POLL_INTERVAL))
                .build(move |job: JsonEnvelope| hand_over(job, tx.clone()));
            // Drive the worker INLINE (not tokio::spawn) so dropping this
            // stream stops the worker and its in-flight handlers.
            let mut worker_fut = std::pin::pin!(worker.run());
            loop {
                tokio::select! {
                    _ = &mut worker_fut => break,
                    maybe = rx.recv() => match maybe {
                        Some((envelope, disp_tx)) => {
                            let receipt = envelope.id.clone();
                            pending.lock().await.insert(receipt.clone(), disp_tx);
                            yield Ok(QueueMessage {
                                id: envelope.id,
                                body: envelope.body,
                                receipt,
                            });
                        }
                        None => break,
                    }
                }
            }
        };
        Box::pin(stream)
    }

    async fn ack(&self, _topic: &str, receipt: &str) -> Result<(), QueueError> {
        // Resolve the worker handler's disposition; the actual Redis ack runs
        // when the handler resumes, which requires the consume() stream to keep
        // being polled — the normal consume-loop shape (next → process → ack →
        // next) satisfies this.
        let disp = self
            .pending
            .lock()
            .await
            .remove(receipt)
            .ok_or_else(|| QueueError::UnknownReceipt(receipt.to_string()))?;
        // A closed receiver means the worker already moved on (e.g. retry
        // timeout reclaimed the job); the ack is then a no-op.
        let _ = disp.send(Disposition::Ack);
        Ok(())
    }

    async fn nack(&self, _topic: &str, receipt: &str, requeue: bool) -> Result<(), QueueError> {
        let disp = self
            .pending
            .lock()
            .await
            .remove(receipt)
            .ok_or_else(|| QueueError::UnknownReceipt(receipt.to_string()))?;
        // requeue=true → retry via apalis's policy; requeue=false → abort to
        // the dead-letter set. Either way apalis owns the job state.
        let decision = if requeue {
            Disposition::Requeue
        } else {
            Disposition::Dlq
        };
        let _ = disp.send(decision);
        Ok(())
    }
}
