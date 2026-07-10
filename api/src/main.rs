use std::net::SocketAddr;
use std::time::Duration;

use api::config::ApiConfig;
use api::error::ApiError;
use api::routes;
use api::state::AppState;
use axum::Router;
use tracing_subscriber::EnvFilter;

use tracing::{info, warn};

const REPLAY_CLEANUP_INTERVAL: Duration = Duration::from_secs(300);
const REPLAY_CLEANUP_BATCH_SIZE: i64 = 1_000;

fn spawn_replay_cleanup(state: AppState) {
    tokio::spawn(async move {
        loop {
            tokio::time::sleep(REPLAY_CLEANUP_INTERVAL).await;
            if state
                .cleanup_expired_dashboard_nonces(REPLAY_CLEANUP_BATCH_SIZE)
                .await
                .is_err()
            {
                warn!("Dashboard replay cleanup was unavailable");
            }
        }
    });
}

#[tokio::main]
async fn main() -> Result<(), ApiError> {
    let config = ApiConfig::from_env().map_err(ApiError::Safe)?;
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::new(config.rust_log.clone()))
        .init();

    let state = AppState::new(config.clone())
        .await
        .map_err(|err| ApiError::Safe(format!("API state initialization failed: {err}")))?;

    let app: Router = routes::create_router(state.clone());
    spawn_replay_cleanup(state);
    let addr: SocketAddr = config
        .api_bind_addr
        .parse()
        .map_err(|err| ApiError::Safe(format!("Invalid API_BIND_ADDR: {err}")))?;

    info!(
        production = config.production_mode,
        bind = %addr,
        "Starting dashboard boundary API"
    );
    let listener = tokio::net::TcpListener::bind(&addr)
        .await
        .map_err(|err| ApiError::Safe(format!("Listen failed: {err}")))?;

    axum::serve(listener, app.into_make_service())
        .await
        .map_err(|err| ApiError::Safe(format!("Server failed: {err}")))?;
    Ok(())
}
