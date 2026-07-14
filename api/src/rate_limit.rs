use std::{
    collections::HashMap,
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};

use crate::error::ApiError;

// Initial per-identity ceilings for the one-minute fixed window.
pub const WORKER_REQUESTS_PER_MINUTE: u32 = 600;
pub const SCHEDULER_REQUESTS_PER_MINUTE: u32 = 60;
pub const OPERATOR_REQUESTS_PER_MINUTE: u32 = 30;
pub const READINESS_REQUESTS_PER_MINUTE: u32 = 60;

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub enum ApiRole {
    Worker,
    Scheduler,
    Operator,
    Readiness,
}

#[derive(Clone)]
pub struct IdentityRateLimiter {
    windows: Arc<Mutex<HashMap<(ApiRole, String), RateWindow>>>,
    window: Duration,
}

struct RateWindow {
    started_at: Instant,
    count: u32,
}

impl IdentityRateLimiter {
    pub fn new(window: Duration) -> Self {
        Self {
            windows: Arc::new(Mutex::new(HashMap::new())),
            window,
        }
    }

    pub fn check(&self, role: ApiRole, identity: &str, limit: u32) -> Result<(), ApiError> {
        self.check_at(role, identity, limit, Instant::now())
    }

    fn check_at(
        &self,
        role: ApiRole,
        identity: &str,
        limit: u32,
        now: Instant,
    ) -> Result<(), ApiError> {
        let mut windows = self.windows.lock().map_err(|_| ApiError::Unavailable)?;
        windows.retain(|_, window| {
            now.checked_duration_since(window.started_at)
                .is_none_or(|elapsed| elapsed < self.window)
        });

        let window = windows
            .entry((role, identity.to_string()))
            .or_insert(RateWindow {
                started_at: now,
                count: 0,
            });
        if window.count >= limit {
            return Err(ApiError::RateLimited);
        }
        window.count = window.count.saturating_add(1);
        Ok(())
    }

    #[cfg(test)]
    fn window_count(&self) -> usize {
        self.windows.lock().expect("rate limit lock").len()
    }
}

#[cfg(test)]
mod tests {
    use std::time::{Duration, Instant};

    use axum::response::IntoResponse;
    use http_body_util::BodyExt;

    use super::{ApiRole, IdentityRateLimiter};
    use crate::error::ApiError;

    #[test]
    fn rate_limits_are_independent_per_identity() {
        let limiter = IdentityRateLimiter::new(std::time::Duration::from_secs(60));
        let now = std::time::Instant::now();
        assert!(limiter
            .check_at(ApiRole::Worker, "worker-a", 1, now)
            .is_ok());
        assert!(matches!(
            limiter.check_at(ApiRole::Worker, "worker-a", 1, now),
            Err(ApiError::RateLimited)
        ));
        assert!(limiter
            .check_at(ApiRole::Worker, "worker-b", 1, now)
            .is_ok());
        assert!(limiter
            .check_at(ApiRole::Scheduler, "worker-a", 1, now)
            .is_ok());
    }

    #[tokio::test]
    async fn rate_limit_returns_429() {
        let limiter = IdentityRateLimiter::new(Duration::from_secs(60));
        let now = Instant::now();
        limiter
            .check_at(ApiRole::Worker, "worker-a", 1, now)
            .unwrap();
        let response = limiter
            .check_at(ApiRole::Worker, "worker-a", 1, now)
            .unwrap_err()
            .into_response();

        assert_eq!(response.status(), axum::http::StatusCode::TOO_MANY_REQUESTS);
        let body = response.into_body().collect().await.unwrap().to_bytes();
        let payload: serde_json::Value = serde_json::from_slice(&body).unwrap();
        assert_eq!(payload["error"]["code"], "rate_limited");
    }

    #[test]
    fn stale_rate_windows_are_evicted() {
        let limiter = IdentityRateLimiter::new(Duration::from_secs(60));
        let now = Instant::now();
        limiter
            .check_at(ApiRole::Worker, "stale-worker", 1, now)
            .unwrap();
        limiter
            .check_at(
                ApiRole::Worker,
                "active-worker",
                1,
                now + Duration::from_secs(61),
            )
            .unwrap();

        assert_eq!(limiter.window_count(), 1);
    }
}
