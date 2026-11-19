//! Background-job queue powered by Apalis + Redis.
//!
//! Define jobs as serde-derived structs with a matching async handler;
//! register them on the worker binary at `src/bin/worker.rs`. Enqueue
//! from request handlers:
//!
//! ```rust,ignore
//! let mut storage = crate::worker::storage().await?;
//! storage.push(HelloJob { name: "forge".into() }).await?;
//! ```

use apalis::prelude::*;
use apalis_redis::RedisStorage;
use serde::{Deserialize, Serialize};

pub fn broker_url() -> String {
    std::env::var("TASKIQ_BROKER_URL").unwrap_or_else(|_| "redis://redis:6379/2".to_string())
}

pub async fn storage<T>() -> Result<RedisStorage<T>, apalis_redis::RedisError>
where
    T: Serialize + for<'de> Deserialize<'de> + Send + Sync + 'static,
{
    let conn = apalis_redis::connect(broker_url()).await?;
    Ok(RedisStorage::new(conn))
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct HelloJob {
    pub name: String,
}

/// Example job handler — replace / extend with project-specific work. An
/// error retries the job after as many seconds as its attempt number (1 s,
/// 2 s, …) until the task's ``max_attempts``, 25 by default; an ``AbortError``
/// sends it to the dead set.
pub async fn handle_hello(job: HelloJob) -> Result<(), BoxDynError> {
    tracing::info!(name = %job.name, "hello_job executed");
    Ok(())
}
