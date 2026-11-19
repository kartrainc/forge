//! Worker binary — runs the Apalis job loop.
//!
//! Launch alongside the main service container:
//!     cargo run --bin worker
//!
//! The worker connects to the Redis queue pointed to by TASKIQ_BROKER_URL
//! and drains `HelloJob` messages. Add more workers by calling
//! `.register(...)` before `.run()`, each building a worker for its job type.

use std::time::Duration;

use apalis::prelude::*;
use dotenvy::dotenv;
use tracing_subscriber::EnvFilter;

use api::worker::{HelloJob, handle_hello, storage};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    dotenv().ok();
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::from_default_env().add_directive("info".parse().unwrap()))
        .json()
        .init();

    let hello_storage = storage::<HelloJob>().await?;

    tracing::info!("worker starting; awaiting jobs...");
    Monitor::new()
        .register(move |_restarts| {
            // apalis 1.x parks an idle worker until something wakes it: the
            // interval picks up jobs the API pushes, and retries falling due.
            WorkerBuilder::new("hello-worker")
                .backend(
                    hello_storage
                        .clone()
                        .poll_with_interval(Duration::from_millis(100)),
                )
                .build(handle_hello)
        })
        .run()
        .await?;
    Ok(())
}
