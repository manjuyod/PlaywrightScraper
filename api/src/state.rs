use std::sync::Arc;

#[cfg(test)]
use std::{collections::HashSet, sync::Mutex};

use chrono::{DateTime, Utc};
use sha2::{Digest, Sha256};
use sqlx::postgres::PgPoolOptions;
use sqlx::PgPool;
use uuid::Uuid;

use crate::config::ApiConfig;
use crate::error::ApiError;
use crate::queries;

#[cfg(test)]
type DashboardReplayTestClaims = Arc<Mutex<HashSet<([u8; 32], Uuid)>>>;

pub fn dashboard_replay_identity_hash(
    franchise_id: &str,
    role: &str,
    user_fingerprint: &str,
) -> [u8; 32] {
    let mut hasher = Sha256::new();
    for field in [franchise_id, role, user_fingerprint] {
        hasher.update((field.len() as u64).to_be_bytes());
        hasher.update(field.as_bytes());
    }
    hasher.finalize().into()
}

#[derive(Clone)]
pub struct AppState {
    pub config: Arc<ApiConfig>,
    pub neon_db: PgPool,
    #[cfg(test)]
    dashboard_replay_test_claims: Option<DashboardReplayTestClaims>,
}

impl AppState {
    pub async fn new(config: ApiConfig) -> Result<Self, ApiError> {
        let neon_db = PgPoolOptions::new()
            .max_connections(4)
            .connect_lazy(&config.neon_database_url)?;

        Ok(Self {
            config: Arc::new(config),
            neon_db,
            #[cfg(test)]
            dashboard_replay_test_claims: None,
        })
    }

    #[cfg(test)]
    pub fn new_test(config: ApiConfig) -> Result<Self, ApiError> {
        let neon_db = PgPoolOptions::new()
            .max_connections(1)
            .connect_lazy(&config.neon_database_url)?;

        Ok(Self {
            config: Arc::new(config),
            neon_db,
            dashboard_replay_test_claims: Some(Arc::new(Mutex::new(HashSet::new()))),
        })
    }

    pub async fn claim_dashboard_nonce(
        &self,
        nonce: Uuid,
        franchise_id: &str,
        role: &str,
        user_fingerprint: &str,
        expires_at: DateTime<Utc>,
    ) -> Result<(), ApiError> {
        let identity_hash = dashboard_replay_identity_hash(franchise_id, role, user_fingerprint);

        #[cfg(test)]
        if let Some(claims) = &self.dashboard_replay_test_claims {
            let mut claims = claims.lock().map_err(|_| ApiError::Unavailable)?;
            return if claims.insert((identity_hash, nonce)) {
                Ok(())
            } else {
                Err(ApiError::Unauthorized)
            };
        }

        if queries::claim_dashboard_nonce(&self.neon_db, &identity_hash, nonce, expires_at).await? {
            Ok(())
        } else {
            Err(ApiError::Unauthorized)
        }
    }

    pub async fn cleanup_expired_dashboard_nonces(&self, batch_size: i64) -> Result<u64, ApiError> {
        queries::cleanup_expired_dashboard_nonces(&self.neon_db, batch_size).await
    }
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use chrono::{Duration as ChronoDuration, Utc};
    use sqlx::postgres::PgPoolOptions;
    use uuid::Uuid;

    fn test_config() -> crate::config::ApiConfig {
        crate::config::ApiConfig {
            neon_database_url: "postgres://127.0.0.1:1/postgres".into(),
            crm_database_url: "server=127.0.0.1;Database=test;Uid=u;Pwd=p;Encrypt=yes;".into(),
            worker_api_tokens: [("worker-test".into(), "worker-secret".into())]
                .into_iter()
                .collect(),
            scheduler_api_tokens: [("scheduler-test".into(), "scheduler-secret".into())]
                .into_iter()
                .collect(),
            operator_api_tokens: [("operator-test".into(), "operator-secret".into())]
                .into_iter()
                .collect(),
            worker_lease_seconds: 300,
            dashboard_hmac_max_age_seconds: 60,
            readiness_api_token: "readiness-secret".into(),
            readiness_timeout_millis: 100,
            production_mode: false,
            api_bind_addr: "127.0.0.1:0".into(),
            dashboard_hmac_verification_keys: crate::config::DashboardHmacVerificationKeys {
                active: "active-secret".into(),
                previous: Some("previous-secret".into()),
            },
            alternate_credential_keyring: crate::credentials::test_keyring(),
            allow_plaintext_alternate_credentials: false,
            rust_log: "info".into(),
        }
    }

    #[tokio::test]
    async fn dashboard_replay_test_backend_binds_nonce_to_the_signed_identity() {
        let state = super::AppState::new_test(test_config()).unwrap();
        let nonce = Uuid::new_v4();
        let expires_at = Utc::now() + ChronoDuration::seconds(60);

        state
            .claim_dashboard_nonce(nonce, "11", "2", "alice", expires_at)
            .await
            .unwrap();
        assert!(matches!(
            state
                .claim_dashboard_nonce(nonce, "11", "2", "alice", expires_at)
                .await,
            Err(crate::error::ApiError::Unauthorized)
        ));
        state
            .claim_dashboard_nonce(nonce, "12", "2", "alice", expires_at)
            .await
            .unwrap();
    }

    #[tokio::test]
    async fn application_state_does_not_connect_to_postgres_during_startup() {
        super::AppState::new(test_config())
            .await
            .expect("lazy state construction must not contact Postgres");
    }

    #[tokio::test]
    async fn database_outage_fails_replay_claim_closed() {
        let pool = PgPoolOptions::new()
            .max_connections(1)
            .acquire_timeout(Duration::from_millis(100))
            .connect_lazy("postgres://127.0.0.1:1/postgres")
            .unwrap();
        let identity_hash = super::dashboard_replay_identity_hash("11", "2", "fingerprint");
        let result = crate::queries::claim_dashboard_nonce(
            &pool,
            &identity_hash,
            Uuid::new_v4(),
            Utc::now() + ChronoDuration::seconds(60),
        )
        .await;
        assert!(matches!(result, Err(crate::error::ApiError::Db(_))));
    }

    #[test]
    fn dashboard_replay_identity_hash_covers_franchise_role_and_user() {
        let original = super::dashboard_replay_identity_hash("11", "2", "fingerprint");
        assert_eq!(original.len(), 32);
        assert_ne!(
            original,
            super::dashboard_replay_identity_hash("12", "2", "fingerprint")
        );
        assert_ne!(
            original,
            super::dashboard_replay_identity_hash("11", "3", "fingerprint")
        );
        assert_ne!(
            original,
            super::dashboard_replay_identity_hash("11", "2", "other")
        );
    }
}
