use std::{collections::HashSet, sync::Arc};

use crate::api_keys::{identify_basic_key, identify_scheduler_key};
use crate::config::DashboardHmacVerificationKeys;
use crate::error::ApiError;
use crate::rate_limit::{
    ApiRole, OPERATOR_REQUESTS_PER_MINUTE, SCHEDULER_REQUESTS_PER_MINUTE,
    WORKER_REQUESTS_PER_MINUTE,
};
use crate::state::AppState;
use axum::{
    body::{to_bytes, Body},
    extract::State,
    http::{HeaderName, Request},
    middleware::Next,
    response::Response,
};
use chrono::Utc;
use hmac::{Hmac, Mac};
use sha2::{Digest, Sha256};
use subtle::ConstantTimeEq;
use uuid::Uuid;

type HmacSha256 = Hmac<Sha256>;

#[derive(Debug, Clone)]
pub struct DashboardAuthClaims {
    pub franchise_id: Option<String>,
    pub role: Option<String>,
    pub user: Option<String>,
}

#[derive(Debug, Clone)]
pub struct WorkerAuthClaims {
    pub worker_id: String,
    pub key_id: String,
    pub lease_token: Option<Uuid>,
}

#[derive(Debug, Clone)]
pub struct SchedulerAuthClaims {
    pub scheduler_id: String,
    pub key_id: String,
    pub franchise_ids: Arc<HashSet<i32>>,
    pub target_worker_ids: Arc<HashSet<String>>,
    pub can_reconcile: bool,
}

#[derive(Debug, Clone)]
pub struct OperatorAuthClaims {
    pub operator_id: String,
    pub key_id: String,
}

const HEADER_TIMESTAMP: HeaderName = HeaderName::from_static("x-api-timestamp");
const HEADER_FRANCHISE_ID: HeaderName = HeaderName::from_static("x-api-franchise-id");
const HEADER_ROLE: HeaderName = HeaderName::from_static("x-api-role");
const HEADER_USER: HeaderName = HeaderName::from_static("x-api-user");
const HEADER_NONCE: HeaderName = HeaderName::from_static("x-api-nonce");
const HEADER_SIGNATURE: HeaderName = HeaderName::from_static("x-api-signature");
const HEADER_AUTHORIZATION: HeaderName = HeaderName::from_static("authorization");
const HEADER_WORKER_LEASE: HeaderName = HeaderName::from_static("x-worker-lease");
const WORKER_CLAIM_PATH: &str = "/api/worker/jobs/claim";
const MAX_DASHBOARD_BODY_BYTES: usize = 1024 * 1024;
const MAX_DASHBOARD_FUTURE_SKEW_SECONDS: i64 = 5;
const DISABLED_PREVIOUS_HMAC_SECRET: &str = "disabled-previous-dashboard-hmac-verifier";

#[derive(Debug, Clone, Copy)]
pub struct DashboardSignatureInput<'a> {
    pub timestamp: &'a str,
    pub method: &'a str,
    pub path_with_query: &'a str,
    pub franchise_id: &'a str,
    pub role: &'a str,
    pub user: &'a str,
    pub nonce: &'a str,
    pub body: &'a [u8],
}

pub fn compute_signature(secret: &str, input: DashboardSignatureInput<'_>) -> String {
    let body_hash = hex::encode(Sha256::digest(input.body));
    let message = format!(
        "{timestamp}\n{}\n{path_with_query}\n{franchise_id}\n{role}\n{user}\n{nonce}\n{body_hash}",
        input.method.to_ascii_uppercase(),
        timestamp = input.timestamp,
        path_with_query = input.path_with_query,
        franchise_id = input.franchise_id,
        role = input.role,
        user = input.user,
        nonce = input.nonce,
    );
    let mut mac = HmacSha256::new_from_slice(secret.as_bytes())
        .expect("HMAC key length is validated by digest algorithm");
    mac.update(message.as_bytes());
    let bytes = mac.finalize().into_bytes();
    hex::encode(bytes)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum DashboardHmacKeyMatch {
    Active,
    Previous,
}

fn matching_dashboard_hmac_key(
    keys: &DashboardHmacVerificationKeys,
    provided_signature: &str,
    input: DashboardSignatureInput<'_>,
) -> Option<DashboardHmacKeyMatch> {
    let active_expected = compute_signature(&keys.active, input);
    let previous_expected = compute_signature(
        keys.previous
            .as_deref()
            .unwrap_or(DISABLED_PREVIOUS_HMAC_SECRET),
        input,
    );
    let active_match = active_expected
        .as_bytes()
        .ct_eq(provided_signature.as_bytes())
        .unwrap_u8();
    let previous_match = previous_expected
        .as_bytes()
        .ct_eq(provided_signature.as_bytes())
        .unwrap_u8()
        & u8::from(keys.previous.is_some());

    if active_match == 1 {
        Some(DashboardHmacKeyMatch::Active)
    } else if previous_match == 1 {
        Some(DashboardHmacKeyMatch::Previous)
    } else {
        None
    }
}

pub async fn dashboard_auth_middleware(
    State(state): State<AppState>,
    req: Request<Body>,
    next: Next,
) -> Result<Response, ApiError> {
    let (mut parts, body) = req.into_parts();
    let body_bytes = to_bytes(body, MAX_DASHBOARD_BODY_BYTES)
        .await
        .map_err(|_| ApiError::BadRequest("Invalid request body".into()))?;

    let uri = parts.uri.clone();
    let method = parts.method.as_str();
    let path_with_query = uri
        .path_and_query()
        .map(|v| v.as_str())
        .unwrap_or(uri.path());

    let timestamp = header_value(&parts.headers, &HEADER_TIMESTAMP, true)?;
    let signature = header_value(&parts.headers, &HEADER_SIGNATURE, false)?;
    let franchise_id = header_value(&parts.headers, &HEADER_FRANCHISE_ID, true)?;
    let role = header_value(&parts.headers, &HEADER_ROLE, true)?;
    let user = header_value(&parts.headers, &HEADER_USER, true)?;
    let nonce_value = header_value(&parts.headers, &HEADER_NONCE, false)?;

    let timestamp_seconds = timestamp
        .parse::<i64>()
        .map_err(|_| ApiError::Unauthorized)?;
    let nonce = Uuid::parse_str(nonce_value).map_err(|_| ApiError::Unauthorized)?;
    let now = Utc::now().timestamp();
    let max_age = state.config.dashboard_hmac_max_age_seconds;
    if timestamp_seconds < now.saturating_sub(max_age)
        || timestamp_seconds > now.saturating_add(MAX_DASHBOARD_FUTURE_SKEW_SECONDS)
    {
        return Err(ApiError::Unauthorized);
    }

    let matched_key = matching_dashboard_hmac_key(
        &state.config.dashboard_hmac_verification_keys,
        signature,
        DashboardSignatureInput {
            timestamp,
            method,
            path_with_query,
            franchise_id,
            role,
            user,
            nonce: nonce_value,
            body: &body_bytes,
        },
    )
    .ok_or(ApiError::Unauthorized)?;
    let _server_selected_key = matched_key;

    let expires_at =
        chrono::DateTime::<Utc>::from_timestamp(timestamp_seconds.saturating_add(max_age), 0)
            .ok_or(ApiError::Unauthorized)?;
    state
        .claim_dashboard_nonce(nonce, franchise_id, role, user, expires_at)
        .await?;

    parts.extensions.insert(DashboardAuthClaims {
        franchise_id: if franchise_id.is_empty() {
            None
        } else {
            Some(franchise_id.to_string())
        },
        role: if role.is_empty() {
            None
        } else {
            Some(role.to_string())
        },
        user: if user.is_empty() {
            None
        } else {
            Some(user.to_string())
        },
    });
    let rebuilt = Request::from_parts(parts, Body::from(body_bytes.to_vec()));
    Ok(next.run(rebuilt).await)
}

pub async fn worker_auth_middleware(
    State(state): State<AppState>,
    mut req: Request<Body>,
    next: Next,
) -> Result<Response, ApiError> {
    let token = bearer_token(req.headers());

    let authenticated = identify_basic_key(&state.config.worker_api_keyring, token, Utc::now())
        .ok_or(ApiError::Unauthorized)?;
    state.rate_limiter.check(
        ApiRole::Worker,
        &authenticated.identity,
        WORKER_REQUESTS_PER_MINUTE,
    )?;
    let lease_token = if req.uri().path() == WORKER_CLAIM_PATH {
        None
    } else {
        Some(
            req.headers()
                .get(HEADER_WORKER_LEASE)
                .and_then(|value| value.to_str().ok())
                .and_then(|value| Uuid::parse_str(value.trim()).ok())
                .ok_or(ApiError::Unauthorized)?,
        )
    };
    req.extensions_mut().insert(WorkerAuthClaims {
        worker_id: authenticated.identity,
        key_id: authenticated.key_id,
        lease_token,
    });

    Ok(next.run(req).await)
}

pub async fn scheduler_auth_middleware(
    State(state): State<AppState>,
    mut req: Request<Body>,
    next: Next,
) -> Result<Response, ApiError> {
    let authenticated = identify_scheduler_key(
        &state.config.scheduler_api_keyring,
        bearer_token(req.headers()),
        Utc::now(),
    )
    .ok_or(ApiError::Unauthorized)?;
    state.rate_limiter.check(
        ApiRole::Scheduler,
        &authenticated.identity,
        SCHEDULER_REQUESTS_PER_MINUTE,
    )?;
    req.extensions_mut().insert(SchedulerAuthClaims {
        scheduler_id: authenticated.identity,
        key_id: authenticated.key_id,
        franchise_ids: authenticated.franchise_ids,
        target_worker_ids: authenticated.target_worker_ids,
        can_reconcile: authenticated.can_reconcile,
    });
    Ok(next.run(req).await)
}

pub async fn operator_auth_middleware(
    State(state): State<AppState>,
    mut req: Request<Body>,
    next: Next,
) -> Result<Response, ApiError> {
    let authenticated = identify_basic_key(
        &state.config.operator_api_keyring,
        bearer_token(req.headers()),
        Utc::now(),
    )
    .ok_or(ApiError::Unauthorized)?;
    state.rate_limiter.check(
        ApiRole::Operator,
        &authenticated.identity,
        OPERATOR_REQUESTS_PER_MINUTE,
    )?;
    req.extensions_mut().insert(OperatorAuthClaims {
        operator_id: authenticated.identity,
        key_id: authenticated.key_id,
    });
    Ok(next.run(req).await)
}

fn bearer_token(headers: &axum::http::HeaderMap) -> &str {
    let auth = headers
        .get(HEADER_AUTHORIZATION)
        .and_then(|value| value.to_str().ok())
        .unwrap_or("")
        .trim();
    auth.strip_prefix("Bearer ")
        .or_else(|| auth.strip_prefix("bearer "))
        .unwrap_or("")
}

fn header_value<'a>(
    headers: &'a axum::http::HeaderMap,
    name: &HeaderName,
    allow_missing: bool,
) -> Result<&'a str, ApiError> {
    let value = headers
        .get(name)
        .and_then(|value| value.to_str().ok())
        .unwrap_or("");
    if allow_missing {
        Ok(value)
    } else if value.is_empty() {
        Err(ApiError::Unauthorized)
    } else {
        Ok(value)
    }
}

#[cfg(test)]
mod tests {
    use axum::{
        body::Body,
        extract::{Extension, State},
        http::{Request, StatusCode},
        middleware,
        response::IntoResponse,
        routing::{get, post},
        Router,
    };
    use chrono::Utc;
    use http_body_util::BodyExt;
    use sha2::{Digest, Sha256};
    use tower::ServiceExt;
    use uuid::Uuid;

    use super::{
        compute_signature, dashboard_auth_middleware, operator_auth_middleware,
        scheduler_auth_middleware, worker_auth_middleware, DashboardSignatureInput,
        OperatorAuthClaims, SchedulerAuthClaims, WorkerAuthClaims,
    };
    use crate::api_keys::{parse_basic_keyring_json, parse_scheduler_keyring_json};
    use crate::config::ApiConfig;
    use crate::state::AppState;

    fn config(tokens: &[(&str, &str)]) -> ApiConfig {
        fn digest(raw: &str) -> String {
            hex::encode(Sha256::digest(raw.as_bytes()))
        }

        let worker_tokens = if tokens.is_empty() {
            vec![("worker-test", "worker-token")]
        } else {
            tokens.to_vec()
        };

        let mut identities = serde_json::Map::with_capacity(worker_tokens.len());
        for (identity, raw_token) in &worker_tokens {
            identities.insert(
                (*identity).to_string(),
                serde_json::json!({
                    "keys": [{
                        "key_id": "primary",
                        "sha256": digest(raw_token),
                        "expires_at": "2099-01-01T00:00:00Z"
                    }]
                }),
            );
        }
        let worker_api_keyring =
            parse_basic_keyring_json(&serde_json::Value::Object(identities).to_string(), "worker")
                .expect("worker keyring fixture");
        let target_worker_ids = worker_tokens
            .iter()
            .map(|(identity, _)| *identity)
            .collect::<Vec<_>>();

        ApiConfig {
            neon_database_url: "postgres://localhost/test".into(),
            crm_database_url: "server=localhost;Database=test".into(),
            worker_api_keyring,
            scheduler_api_keyring: parse_scheduler_keyring_json(
                &serde_json::json!({
                    "scheduler-test": {
                        "keys": [{
                            "key_id": "primary",
                            "sha256": digest("scheduler-secret"),
                            "expires_at": "2099-01-01T00:00:00Z"
                        }],
                        "franchise_ids": [11],
                        "target_worker_ids": target_worker_ids,
                        "can_reconcile": false
                    }
                })
                .to_string(),
            )
            .expect("scheduler keyring fixture"),
            operator_api_keyring: parse_basic_keyring_json(
                &serde_json::json!({
                    "operator-test": {
                        "keys": [{
                            "key_id": "primary",
                            "sha256": digest("operator-secret"),
                            "expires_at": "2099-01-01T00:00:00Z"
                        }]
                    }
                })
                .to_string(),
                "operator",
            )
            .expect("operator keyring fixture"),
            readiness_api_keyring: parse_basic_keyring_json(
                &serde_json::json!({
                    "readiness-test": {
                        "keys": [{
                            "key_id": "primary",
                            "sha256": digest("readiness-secret"),
                            "expires_at": "2099-01-01T00:00:00Z"
                        }]
                    }
                })
                .to_string(),
                "readiness",
            )
            .expect("readiness keyring fixture"),
            worker_lease_seconds: 300,
            dashboard_hmac_max_age_seconds: 60,
            default_worker_id: worker_tokens
                .first()
                .map(|(identity, _)| (*identity).to_string())
                .unwrap_or_else(|| "worker-test".to_string()),
            readiness_timeout_millis: 100,
            production_mode: false,
            api_bind_addr: "127.0.0.1:0".into(),
            dashboard_hmac_verification_keys: crate::config::DashboardHmacVerificationKeys {
                active: "test-secret".into(),
                previous: None,
            },
            alternate_credential_keyring: crate::credentials::test_keyring(),
            allow_plaintext_alternate_credentials: false,
            rust_log: "info".into(),
        }
    }

    #[test]
    fn worker_token_identity_is_derived_only_from_a_unique_bearer_token() {
        fn digest(raw: &str) -> String {
            hex::encode(Sha256::digest(raw.as_bytes()))
        }

        let api_config = config(&[("worker-a", "token-a"), ("worker-b", "token-b")]);
        let worker_b = crate::api_keys::identify_basic_key(
            &api_config.worker_api_keyring,
            "token-b",
            Utc::now(),
        )
        .unwrap();
        assert_eq!(worker_b.identity, "worker-b");
        assert!(crate::api_keys::identify_basic_key(
            &api_config.worker_api_keyring,
            "",
            Utc::now()
        )
        .is_none());
        assert!(crate::api_keys::identify_basic_key(
            &api_config.worker_api_keyring,
            "unknown",
            Utc::now()
        )
        .is_none());

        assert!(parse_basic_keyring_json(
            &serde_json::json!({
                "worker-a": {
                    "keys": [{
                        "key_id": "primary",
                        "sha256": digest("shared"),
                        "expires_at": "2099-01-01T00:00:00Z"
                    }]
                },
                "worker-b": {
                    "keys": [{
                        "key_id": "primary",
                        "sha256": digest("shared"),
                        "expires_at": "2099-01-01T00:00:00Z"
                    }]
                }
            })
            .to_string(),
            "worker"
        )
        .is_err());
    }

    #[test]
    fn operator_identity_is_derived_only_from_the_operator_token_map() {
        let api_config = config(&[("worker-a", "worker-token")]);
        let operator = crate::api_keys::identify_basic_key(
            &api_config.operator_api_keyring,
            "operator-secret",
            Utc::now(),
        )
        .unwrap();
        assert_eq!(operator.identity, "operator-test");
        assert!(crate::api_keys::identify_basic_key(
            &api_config.operator_api_keyring,
            "worker-token",
            Utc::now()
        )
        .is_none());
    }

    #[test]
    fn dashboard_signature_verifier_computes_active_and_previous_candidates() {
        let input = DashboardSignatureInput {
            timestamp: "1700000000",
            method: "POST",
            path_with_query: "/api/jobs/manual-pull",
            franchise_id: "11",
            role: "2",
            user: "user-fingerprint",
            nonce: "00000000-0000-0000-0000-000000000042",
            body: br#"{"kind":"grade"}"#,
        };
        let keys = crate::config::DashboardHmacVerificationKeys {
            active: "active-secret".into(),
            previous: Some("previous-secret".into()),
        };
        let previous_signature = compute_signature("previous-secret", input);

        assert_eq!(
            super::matching_dashboard_hmac_key(&keys, &previous_signature, input),
            Some(super::DashboardHmacKeyMatch::Previous)
        );
        assert_eq!(
            super::matching_dashboard_hmac_key(&keys, "not-a-signature", input),
            None
        );
    }

    #[test]
    fn rust_matches_the_shared_python_hmac_vector() {
        let vectors: serde_json::Value =
            serde_json::from_str(include_str!("../testdata/dashboard_hmac_vectors.json")).unwrap();
        let vector = &vectors[0];
        let body = vector["body"].as_str().unwrap().as_bytes();
        let signature = compute_signature(
            vector["secret"].as_str().unwrap(),
            DashboardSignatureInput {
                timestamp: vector["timestamp"].as_str().unwrap(),
                method: vector["method"].as_str().unwrap(),
                path_with_query: vector["path"].as_str().unwrap(),
                franchise_id: vector["franchise_id"].as_str().unwrap(),
                role: vector["role"].as_str().unwrap(),
                user: vector["user"].as_str().unwrap(),
                nonce: vector["nonce"].as_str().unwrap(),
                body,
            },
        );
        assert_eq!(signature, vector["signature"].as_str().unwrap());
    }

    async fn worker_identity_probe(
        State(_state): State<AppState>,
        Extension(claims): Extension<WorkerAuthClaims>,
    ) -> impl IntoResponse {
        claims.worker_id
    }

    async fn operator_identity_probe(
        Extension(claims): Extension<OperatorAuthClaims>,
    ) -> impl IntoResponse {
        claims.operator_id
    }

    async fn scheduler_policy_probe(
        Extension(claims): Extension<SchedulerAuthClaims>,
    ) -> impl IntoResponse {
        axum::Json(serde_json::json!({
            "scheduler_id": claims.scheduler_id,
            "key_id": claims.key_id,
            "franchise_ids": claims.franchise_ids,
            "target_worker_ids": claims.target_worker_ids,
            "can_reconcile": claims.can_reconcile,
        }))
    }

    #[tokio::test]
    async fn operator_middleware_accepts_only_operator_tokens() {
        let state = AppState::new_test(config(&[("worker-a", "worker-token")])).unwrap();
        let app = Router::new()
            .route("/probe", get(operator_identity_probe))
            .route_layer(middleware::from_fn_with_state(
                state.clone(),
                operator_auth_middleware,
            ))
            .with_state(state);

        let accepted = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri("/probe")
                    .header("authorization", "Bearer operator-secret")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        let rejected = app
            .oneshot(
                Request::builder()
                    .uri("/probe")
                    .header("authorization", "Bearer worker-token")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();

        assert_eq!(accepted.status(), StatusCode::OK);
        assert_eq!(rejected.status(), StatusCode::UNAUTHORIZED);
    }

    #[tokio::test]
    async fn worker_middleware_inserts_identity_claims_from_the_bearer_token() {
        let state = AppState::new_test(config(&[("worker-a", "token-a")])).unwrap();
        let app = Router::new()
            .route("/probe", get(worker_identity_probe))
            .route_layer(middleware::from_fn_with_state(
                state.clone(),
                worker_auth_middleware,
            ))
            .with_state(state);

        let response = app
            .oneshot(
                Request::builder()
                    .uri("/probe")
                    .header("authorization", "Bearer token-a")
                    .header("x-worker-lease", "00000000-0000-0000-0000-000000000042")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
    }

    #[tokio::test]
    async fn expired_key_is_unauthorized() {
        let mut api_config = config(&[("worker-a", "expired-key")]);
        api_config
            .worker_api_keyring
            .get_mut("worker-a")
            .unwrap()
            .keys[0]
            .expires_at = Utc::now() - chrono::Duration::seconds(1);
        let state = AppState::new_test(api_config).unwrap();
        let app = Router::new()
            .route("/probe", get(worker_identity_probe))
            .route_layer(middleware::from_fn_with_state(
                state.clone(),
                worker_auth_middleware,
            ))
            .with_state(state);

        let response = app
            .oneshot(
                Request::builder()
                    .uri("/probe")
                    .header("authorization", "Bearer expired-key")
                    .header("x-worker-lease", "00000000-0000-0000-0000-000000000042")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    }

    #[tokio::test]
    async fn scheduler_claims_carry_policy() {
        let state = AppState::new_test(config(&[("worker-a", "worker-key")])).unwrap();
        let app = Router::new()
            .route("/probe", get(scheduler_policy_probe))
            .route_layer(middleware::from_fn_with_state(
                state.clone(),
                scheduler_auth_middleware,
            ))
            .with_state(state);

        let response = app
            .oneshot(
                Request::builder()
                    .uri("/probe")
                    .header("authorization", "Bearer scheduler-secret")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let body = response.into_body().collect().await.unwrap().to_bytes();
        let payload: serde_json::Value = serde_json::from_slice(&body).unwrap();
        assert_eq!(payload["scheduler_id"], "scheduler-test");
        assert_eq!(payload["key_id"], "primary");
        assert_eq!(payload["franchise_ids"], serde_json::json!([11]));
        assert_eq!(
            payload["target_worker_ids"],
            serde_json::json!(["worker-a"])
        );
        assert_eq!(payload["can_reconcile"], false);
    }

    #[tokio::test]
    async fn worker_middleware_requires_a_valid_lease_for_post_claim_routes() {
        let state = AppState::new_test(config(&[("worker-a", "token-a")])).unwrap();
        let app = Router::new()
            .route("/probe", get(worker_identity_probe))
            .route_layer(middleware::from_fn_with_state(
                state.clone(),
                worker_auth_middleware,
            ))
            .with_state(state);

        for lease in [None, Some("not-a-uuid")] {
            let mut request = Request::builder()
                .uri("/probe")
                .header("authorization", "Bearer token-a");
            if let Some(lease) = lease {
                request = request.header("x-worker-lease", lease);
            }
            let response = app
                .clone()
                .oneshot(request.body(Body::empty()).unwrap())
                .await
                .unwrap();
            assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
        }
    }

    fn dashboard_app(state: AppState) -> Router {
        Router::new()
            .route("/probe", post(|| async { StatusCode::OK }))
            .route_layer(middleware::from_fn_with_state(
                state.clone(),
                dashboard_auth_middleware,
            ))
            .with_state(state)
    }

    #[allow(clippy::too_many_arguments)]
    fn signed_dashboard_request(
        state: &AppState,
        timestamp: &str,
        signed_user: &str,
        sent_user: &str,
        signed_nonce: &str,
        sent_nonce: &str,
        signed_body: &[u8],
        sent_body: Vec<u8>,
    ) -> Request<Body> {
        let signature = compute_signature(
            &state.config.dashboard_hmac_verification_keys.active,
            DashboardSignatureInput {
                timestamp,
                method: "POST",
                path_with_query: "/probe",
                franchise_id: "11",
                role: "2",
                user: signed_user,
                nonce: signed_nonce,
                body: signed_body,
            },
        );
        Request::builder()
            .method("POST")
            .uri("/probe")
            .header("x-api-timestamp", timestamp)
            .header("x-api-franchise-id", "11")
            .header("x-api-role", "2")
            .header("x-api-user", sent_user)
            .header("x-api-nonce", sent_nonce)
            .header("x-api-signature", signature)
            .body(Body::from(sent_body))
            .unwrap()
    }

    #[tokio::test]
    async fn dashboard_middleware_rejects_replayed_nonce_after_valid_signature() {
        let state = AppState::new_test(config(&[])).unwrap();
        let app = dashboard_app(state.clone());
        let timestamp = Utc::now().timestamp().to_string();
        let nonce = Uuid::new_v4().to_string();
        let body = br#"{"kind":"grade"}"#.to_vec();

        let first = app
            .clone()
            .oneshot(signed_dashboard_request(
                &state,
                &timestamp,
                "alice",
                "alice",
                &nonce,
                &nonce,
                &body,
                body.clone(),
            ))
            .await
            .unwrap();
        let replay = app
            .oneshot(signed_dashboard_request(
                &state,
                &timestamp,
                "alice",
                "alice",
                &nonce,
                &nonce,
                &body,
                body.clone(),
            ))
            .await
            .unwrap();

        assert_eq!(first.status(), StatusCode::OK);
        assert_eq!(replay.status(), StatusCode::UNAUTHORIZED);
    }

    #[tokio::test]
    async fn dashboard_middleware_rejects_stale_timestamps_after_signature_validation() {
        let state = AppState::new_test(config(&[])).unwrap();
        let timestamp =
            (Utc::now().timestamp() - state.config.dashboard_hmac_max_age_seconds - 1).to_string();
        let nonce = Uuid::new_v4().to_string();
        let response = dashboard_app(state.clone())
            .oneshot(signed_dashboard_request(
                &state,
                &timestamp,
                "alice",
                "alice",
                &nonce,
                &nonce,
                b"{}",
                b"{}".to_vec(),
            ))
            .await
            .unwrap();

        assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    }

    #[tokio::test]
    async fn dashboard_middleware_allows_only_five_seconds_of_future_clock_skew() {
        let state = AppState::new_test(config(&[])).unwrap();
        let timestamp = (Utc::now().timestamp() + 6).to_string();
        let nonce = Uuid::new_v4().to_string();
        let response = dashboard_app(state.clone())
            .oneshot(signed_dashboard_request(
                &state,
                &timestamp,
                "alice",
                "alice",
                &nonce,
                &nonce,
                b"{}",
                b"{}".to_vec(),
            ))
            .await
            .unwrap();

        assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    }

    #[tokio::test]
    async fn dashboard_middleware_requires_a_well_formed_uuid_nonce() {
        let state = AppState::new_test(config(&[])).unwrap();
        let timestamp = Utc::now().timestamp().to_string();
        let response = dashboard_app(state.clone())
            .oneshot(signed_dashboard_request(
                &state,
                &timestamp,
                "alice",
                "alice",
                "not-a-uuid",
                "not-a-uuid",
                b"{}",
                b"{}".to_vec(),
            ))
            .await
            .unwrap();

        assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    }

    #[tokio::test]
    async fn dashboard_middleware_binds_the_signature_to_user_nonce_and_body() {
        let state = AppState::new_test(config(&[])).unwrap();
        let timestamp = Utc::now().timestamp().to_string();
        let body = br#"{"kind":"grade"}"#;
        let mutated_body = br#"{"kind":"agenda"}"#;
        let user_nonce = Uuid::new_v4().to_string();
        let signed_nonce = Uuid::new_v4().to_string();
        let nonce_mutation = format!("{signed_nonce} ");
        let body_nonce = Uuid::new_v4().to_string();

        #[allow(clippy::type_complexity)]
        let cases: Vec<(&str, &str, String, String, &[u8], Vec<u8>)> = vec![
            (
                "alice",
                "alice ",
                user_nonce.clone(),
                user_nonce,
                body.as_slice(),
                body.to_vec(),
            ),
            (
                "alice",
                "alice",
                signed_nonce,
                nonce_mutation,
                body.as_slice(),
                body.to_vec(),
            ),
            (
                "alice",
                "alice",
                body_nonce.clone(),
                body_nonce,
                body.as_slice(),
                mutated_body.to_vec(),
            ),
        ];

        for (signed_user, sent_user, signed_nonce, sent_nonce, signed_body, sent_body) in cases {
            let request = signed_dashboard_request(
                &state,
                &timestamp,
                signed_user,
                sent_user,
                &signed_nonce,
                &sent_nonce,
                signed_body,
                sent_body,
            );
            let response = dashboard_app(state.clone()).oneshot(request).await.unwrap();
            assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
        }
    }

    #[tokio::test]
    async fn dashboard_middleware_enforces_the_one_mebibyte_body_limit() {
        let state = AppState::new_test(config(&[])).unwrap();
        let timestamp = Utc::now().timestamp().to_string();
        let nonce = Uuid::new_v4().to_string();
        let boundary = vec![b'a'; 1024 * 1024];
        let accepted = dashboard_app(state.clone())
            .oneshot(signed_dashboard_request(
                &state,
                &timestamp,
                "alice",
                "alice",
                &nonce,
                &nonce,
                &boundary,
                boundary.clone(),
            ))
            .await
            .unwrap();
        let too_large = dashboard_app(state)
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/probe")
                    .body(Body::from(vec![b'a'; 1024 * 1024 + 1]))
                    .unwrap(),
            )
            .await
            .unwrap();

        assert_eq!(accepted.status(), StatusCode::OK);
        assert_eq!(too_large.status(), StatusCode::BAD_REQUEST);
    }
}
