use std::{collections::HashMap, sync::Arc};

use api::api_keys::{parse_basic_keyring_json, parse_scheduler_keyring_json};
use api::config::{ApiConfig, DashboardHmacVerificationKeys};
use api::credentials::AlternateCredentialKeyring;
use api::crm::{CrmGateway, CrmLogin};
use api::error::ApiError;
use api::models::CrmStudent;
use api::routes::create_router;
use api::state::AppState;
use axum::body::Body;
use axum::http::{Method, Request, StatusCode};
use axum::response::Response;
use axum::Router;
use http_body_util::BodyExt;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use sqlx::PgPool;
use tower::ServiceExt;
use uuid::Uuid;

const SCHEDULER_TOKEN: &str = "scheduler-alice-secret";
const OPERATOR_TOKEN: &str = "operator-alice-secret";
const DEV_WORKER_ID: &str = "dev-alice-laptop";
const DEV_WORKER_TOKEN: &str = "dev-alice-worker-secret";
const PROD_WORKER_ID: &str = "prod-windows-01";
const PROD_WORKER_TOKEN: &str = "prod-windows-worker-secret";

#[derive(Clone)]
struct FakeCrmGateway {
    students: Vec<CrmStudent>,
}

#[async_trait::async_trait]
impl CrmGateway for FakeCrmGateway {
    async fn ping(&self) -> Result<(), ApiError> {
        Ok(())
    }

    async fn login(&self, username: &str, password: &str) -> Result<CrmLogin, ApiError> {
        let authenticated = !username.is_empty() && !password.is_empty();
        Ok(CrmLogin {
            authenticated,
            role: authenticated.then_some(2),
            franchise_id: authenticated.then_some(11),
            display_name: authenticated.then(|| "Test User".to_string()),
        })
    }

    async fn list_students(
        &self,
        franchise_id: Option<i32>,
        student_id: Option<i64>,
    ) -> Result<Vec<CrmStudent>, ApiError> {
        Ok(self
            .students
            .iter()
            .filter(|student| {
                franchise_id.is_none_or(|value| student.franchiseid == value)
                    && student_id.is_none_or(|value| student.crmstudentid == value)
            })
            .cloned()
            .collect())
    }
}

fn digest(raw: &str) -> String {
    hex::encode(Sha256::digest(raw.as_bytes()))
}

fn test_config() -> ApiConfig {
    ApiConfig {
        neon_database_url: "postgres://unused/test".into(),
        crm_database_url: "server=unused;Database=test".into(),
        worker_api_keyring: parse_basic_keyring_json(
            &json!({
                DEV_WORKER_ID: {
                    "keys": [{
                        "key_id": "primary",
                        "sha256": digest(DEV_WORKER_TOKEN),
                        "expires_at": "2099-01-01T00:00:00Z"
                    }]
                },
                PROD_WORKER_ID: {
                    "keys": [{
                        "key_id": "primary",
                        "sha256": digest(PROD_WORKER_TOKEN),
                        "expires_at": "2099-01-01T00:00:00Z"
                    }]
                }
            })
            .to_string(),
            "worker",
        )
        .unwrap(),
        scheduler_api_keyring: parse_scheduler_keyring_json(
            &json!({
                "scheduler-alice": {
                    "keys": [{
                        "key_id": "primary",
                        "sha256": digest(SCHEDULER_TOKEN),
                        "expires_at": "2099-01-01T00:00:00Z"
                    }],
                    "franchise_ids": [11],
                    "target_worker_ids": [DEV_WORKER_ID, PROD_WORKER_ID],
                    "can_reconcile": false
                }
            })
            .to_string(),
        )
        .unwrap(),
        operator_api_keyring: parse_basic_keyring_json(
            &json!({
                "operator-alice": {
                    "keys": [{
                        "key_id": "primary",
                        "sha256": digest(OPERATOR_TOKEN),
                        "expires_at": "2099-01-01T00:00:00Z"
                    }]
                }
            })
            .to_string(),
            "operator",
        )
        .unwrap(),
        readiness_api_keyring: parse_basic_keyring_json(
            &json!({
                "readiness-test": {
                    "keys": [{
                        "key_id": "primary",
                        "sha256": digest("readiness-test-secret"),
                        "expires_at": "2099-01-01T00:00:00Z"
                    }]
                }
            })
            .to_string(),
            "readiness",
        )
        .unwrap(),
        default_worker_id: PROD_WORKER_ID.into(),
        worker_lease_seconds: 300,
        dashboard_hmac_max_age_seconds: 60,
        readiness_timeout_millis: 100,
        production_mode: false,
        api_bind_addr: "127.0.0.1:0".into(),
        dashboard_hmac_verification_keys: DashboardHmacVerificationKeys {
            active: "dashboard-test-secret".into(),
            previous: None,
        },
        alternate_credential_keyring: AlternateCredentialKeyring::new(
            "test-key",
            HashMap::from([("test-key".into(), [7_u8; 32])]),
            "test",
        )
        .unwrap(),
        allow_plaintext_alternate_credentials: false,
        rust_log: "info".into(),
    }
}

fn fake_crm(students: Vec<CrmStudent>) -> Arc<dyn CrmGateway> {
    Arc::new(FakeCrmGateway { students })
}

fn test_router(pool: PgPool, students: Vec<CrmStudent>) -> Router {
    create_router(AppState::with_dependencies(
        test_config(),
        pool,
        fake_crm(students),
    ))
}

fn student(student_id: i64) -> CrmStudent {
    CrmStudent {
        crmstudentid: student_id,
        franchiseid: 11,
        firstname: format!("Student {student_id}"),
        lastname: "Example".into(),
        grade: Some(8),
        portal1: Some("https://portal.example.test".into()),
        p1username: Some(format!("student-{student_id}")),
        p1password: Some("portal-password".into()),
        franchise_name: Some("Test Center".into()),
    }
}

#[derive(Clone, Copy)]
enum TestTable {
    Results,
    StudentState,
}

async fn row_count(pool: &PgPool, table: TestTable) -> i64 {
    match table {
        TestTable::Results => sqlx::query_scalar("SELECT COUNT(*) FROM grade_scrape_results")
            .fetch_one(pool)
            .await
            .unwrap(),
        TestTable::StudentState => {
            sqlx::query_scalar("SELECT COUNT(*) FROM students_grades_20262027")
                .fetch_one(pool)
                .await
                .unwrap()
        }
    }
}

async fn send(
    app: &Router,
    method: Method,
    uri: &str,
    bearer: Option<&str>,
    lease: Option<Uuid>,
    body: Option<Value>,
) -> Response {
    let mut builder = Request::builder().method(method).uri(uri);
    if let Some(token) = bearer {
        builder = builder.header("authorization", format!("Bearer {token}"));
    }
    if let Some(lease) = lease {
        builder = builder.header("x-worker-lease", lease.to_string());
    }
    let body = if let Some(body) = body {
        builder = builder.header("content-type", "application/json");
        Body::from(body.to_string())
    } else {
        Body::empty()
    };
    app.clone()
        .oneshot(builder.body(body).unwrap())
        .await
        .unwrap()
}

async fn send_raw_result(app: &Router, job_id: Uuid, lease: Uuid, body: &str) -> Response {
    app.clone()
        .oneshot(
            Request::builder()
                .method(Method::POST)
                .uri(format!("/api/worker/jobs/{job_id}/results"))
                .header("authorization", format!("Bearer {DEV_WORKER_TOKEN}"))
                .header("x-worker-lease", lease.to_string())
                .header("content-type", "application/json")
                .body(Body::from(body.to_string()))
                .unwrap(),
        )
        .await
        .unwrap()
}

async fn response_json(response: Response) -> Value {
    let bytes = response.into_body().collect().await.unwrap().to_bytes();
    serde_json::from_slice(&bytes).unwrap()
}

async fn enqueue(
    app: &Router,
    target_worker_id: &str,
    student_id: Option<i64>,
    idempotency_key: Uuid,
) -> Response {
    send(
        app,
        Method::POST,
        "/api/scheduler/jobs",
        Some(SCHEDULER_TOKEN),
        None,
        Some(json!({
            "idempotency_key": idempotency_key,
            "kind": "grade",
            "franchise_id": 11,
            "student_id": student_id,
            "target_worker_id": target_worker_id,
        })),
    )
    .await
}

async fn enqueue_job(app: &Router, target_worker_id: &str, student_id: Option<i64>) -> Uuid {
    let response = enqueue(app, target_worker_id, student_id, Uuid::new_v4()).await;
    assert_eq!(response.status(), StatusCode::OK);
    Uuid::parse_str(response_json(response).await["id"].as_str().unwrap()).unwrap()
}

async fn claim(app: &Router, token: &str) -> Option<(Uuid, Uuid)> {
    let response = send(
        app,
        Method::POST,
        "/api/worker/jobs/claim",
        Some(token),
        None,
        None,
    )
    .await;
    if response.status() == StatusCode::NO_CONTENT {
        return None;
    }
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    Some((
        Uuid::parse_str(body["job_id"].as_str().unwrap()).unwrap(),
        Uuid::parse_str(body["lease_token"].as_str().unwrap()).unwrap(),
    ))
}

fn result_body(student_id: i64, idempotency_key: Uuid, status: &str) -> Value {
    json!({
        "crmstudentid": student_id,
        "idempotency_key": idempotency_key,
        "status": status,
        "passwordgood": status == "synced",
        "parsed_grades": {"math": "A"},
    })
}

async fn post_result(
    app: &Router,
    job_id: Uuid,
    token: &str,
    lease: Uuid,
    body: Value,
) -> Response {
    send(
        app,
        Method::POST,
        &format!("/api/worker/jobs/{job_id}/results"),
        Some(token),
        Some(lease),
        Some(body),
    )
    .await
}

#[sqlx::test(migrations = "./migrations")]
async fn scoped_scheduler_to_targeted_worker_result_lifecycle(pool: PgPool) {
    let app = test_router(pool.clone(), vec![student(42)]);
    let job_id = enqueue_job(&app, DEV_WORKER_ID, Some(42)).await;

    assert!(claim(&app, PROD_WORKER_TOKEN).await.is_none());
    let (claimed_job_id, lease) = claim(&app, DEV_WORKER_TOKEN).await.unwrap();
    assert_eq!(claimed_job_id, job_id);

    let context = send(
        &app,
        Method::GET,
        &format!("/api/worker/jobs/{job_id}/context"),
        Some(DEV_WORKER_TOKEN),
        Some(lease),
        None,
    )
    .await;
    assert_eq!(context.status(), StatusCode::OK);
    let context = response_json(context).await;
    assert_eq!(context["students"][0]["crmstudentid"], 42);

    let heartbeat = send(
        &app,
        Method::POST,
        &format!("/api/worker/jobs/{job_id}/heartbeat"),
        Some(DEV_WORKER_TOKEN),
        Some(lease),
        Some(json!({
            "kind": "grade", "total": 1, "attempted": 0, "success": 0, "errors": 0
        })),
    )
    .await;
    assert_eq!(heartbeat.status(), StatusCode::ACCEPTED);

    let event = send(
        &app,
        Method::POST,
        &format!("/api/worker/jobs/{job_id}/events"),
        Some(DEV_WORKER_TOKEN),
        Some(lease),
        Some(json!({"code": "student_started", "crmstudentid": 42})),
    )
    .await;
    assert_eq!(event.status(), StatusCode::ACCEPTED);

    let result_id = Uuid::new_v4();
    let result = post_result(
        &app,
        job_id,
        DEV_WORKER_TOKEN,
        lease,
        result_body(42, result_id, "synced"),
    )
    .await;
    assert_eq!(result.status(), StatusCode::ACCEPTED);
    assert_eq!(row_count(&pool, TestTable::Results).await, 1);
    assert_eq!(row_count(&pool, TestTable::StudentState).await, 1);

    let attribution: (Option<String>, Option<String>, bool, Option<String>) = sqlx::query_as(
        "SELECT target_worker_id, worker_id, lease_expires_at > NOW(), scheduler_identity FROM grade_scrape_jobs WHERE id = $1",
    )
    .bind(job_id)
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(attribution.0.as_deref(), Some(DEV_WORKER_ID));
    assert_eq!(attribution.1.as_deref(), Some(DEV_WORKER_ID));
    assert!(attribution.2);
    assert_eq!(attribution.3.as_deref(), Some("scheduler-alice"));
    let result_worker: Option<String> = sqlx::query_scalar(
        "SELECT jobs.worker_id FROM grade_scrape_results results JOIN grade_scrape_jobs jobs ON jobs.id = results.job_id WHERE results.idempotency_key = $1",
    )
    .bind(result_id)
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(result_worker.as_deref(), Some(DEV_WORKER_ID));
}

#[sqlx::test(migrations = "./migrations")]
async fn production_worker_cannot_claim_local_target(pool: PgPool) {
    let app = test_router(pool, vec![student(42)]);
    let job_id = enqueue_job(&app, DEV_WORKER_ID, Some(42)).await;

    assert!(claim(&app, PROD_WORKER_TOKEN).await.is_none());
    assert_eq!(claim(&app, DEV_WORKER_TOKEN).await.unwrap().0, job_id);
}

#[sqlx::test(migrations = "./migrations")]
async fn wrong_worker_and_wrong_lease_cannot_read_context_or_write_result(pool: PgPool) {
    let app = test_router(pool.clone(), vec![student(42)]);
    let job_id = enqueue_job(&app, DEV_WORKER_ID, Some(42)).await;
    let (_, lease) = claim(&app, DEV_WORKER_TOKEN).await.unwrap();
    let result_id = Uuid::new_v4();

    for (token, attempted_lease) in [
        (PROD_WORKER_TOKEN, lease),
        (DEV_WORKER_TOKEN, Uuid::new_v4()),
    ] {
        let context = send(
            &app,
            Method::GET,
            &format!("/api/worker/jobs/{job_id}/context"),
            Some(token),
            Some(attempted_lease),
            None,
        )
        .await;
        assert_eq!(context.status(), StatusCode::NOT_FOUND);
        let result = post_result(
            &app,
            job_id,
            token,
            attempted_lease,
            result_body(42, result_id, "synced"),
        )
        .await;
        assert_eq!(result.status(), StatusCode::NOT_FOUND);
    }
    assert_eq!(row_count(&pool, TestTable::Results).await, 0);
    assert_eq!(row_count(&pool, TestTable::StudentState).await, 0);
}

#[sqlx::test(migrations = "./migrations")]
async fn expired_lease_cannot_write_result(pool: PgPool) {
    let app = test_router(pool.clone(), vec![student(42)]);
    let job_id = enqueue_job(&app, DEV_WORKER_ID, Some(42)).await;
    let (_, lease) = claim(&app, DEV_WORKER_TOKEN).await.unwrap();
    sqlx::query(
        "UPDATE grade_scrape_jobs SET lease_expires_at = NOW() - INTERVAL '1 second' WHERE id = $1",
    )
    .bind(job_id)
    .execute(&pool)
    .await
    .unwrap();

    let response = post_result(
        &app,
        job_id,
        DEV_WORKER_TOKEN,
        lease,
        result_body(42, Uuid::new_v4(), "synced"),
    )
    .await;
    assert_eq!(response.status(), StatusCode::NOT_FOUND);
    assert_eq!(row_count(&pool, TestTable::Results).await, 0);
    assert_eq!(row_count(&pool, TestTable::StudentState).await, 0);
}

#[sqlx::test(migrations = "./migrations")]
async fn identical_result_retry_is_idempotent(pool: PgPool) {
    let app = test_router(pool.clone(), vec![student(42)]);
    let job_id = enqueue_job(&app, DEV_WORKER_ID, Some(42)).await;
    let (_, lease) = claim(&app, DEV_WORKER_TOKEN).await.unwrap();
    let body = result_body(42, Uuid::new_v4(), "synced");

    for _ in 0..2 {
        let response = post_result(&app, job_id, DEV_WORKER_TOKEN, lease, body.clone()).await;
        assert_eq!(response.status(), StatusCode::ACCEPTED);
    }
    assert_eq!(row_count(&pool, TestTable::Results).await, 1);
    assert_eq!(row_count(&pool, TestTable::StudentState).await, 1);
}

#[sqlx::test(migrations = "./migrations")]
async fn changed_result_retry_is_conflict(pool: PgPool) {
    let app = test_router(pool.clone(), vec![student(42)]);
    let job_id = enqueue_job(&app, DEV_WORKER_ID, Some(42)).await;
    let (_, lease) = claim(&app, DEV_WORKER_TOKEN).await.unwrap();
    let result_id = Uuid::new_v4();
    assert_eq!(
        post_result(
            &app,
            job_id,
            DEV_WORKER_TOKEN,
            lease,
            result_body(42, result_id, "synced"),
        )
        .await
        .status(),
        StatusCode::ACCEPTED
    );

    let changed = post_result(
        &app,
        job_id,
        DEV_WORKER_TOKEN,
        lease,
        result_body(42, result_id, "bad_login"),
    )
    .await;
    assert_eq!(changed.status(), StatusCode::CONFLICT);
    assert_eq!(row_count(&pool, TestTable::Results).await, 1);
    assert_eq!(row_count(&pool, TestTable::StudentState).await, 1);
}

#[sqlx::test(migrations = "./migrations")]
async fn reused_result_uuid_for_different_student_is_conflict(pool: PgPool) {
    let app = test_router(pool.clone(), vec![student(41), student(42)]);
    let job_id = enqueue_job(&app, DEV_WORKER_ID, None).await;
    let (_, lease) = claim(&app, DEV_WORKER_TOKEN).await.unwrap();
    let result_id = Uuid::new_v4();
    assert_eq!(
        post_result(
            &app,
            job_id,
            DEV_WORKER_TOKEN,
            lease,
            result_body(41, result_id, "synced"),
        )
        .await
        .status(),
        StatusCode::ACCEPTED
    );

    let reused = post_result(
        &app,
        job_id,
        DEV_WORKER_TOKEN,
        lease,
        result_body(42, result_id, "synced"),
    )
    .await;
    assert_eq!(reused.status(), StatusCode::CONFLICT);
    assert_eq!(row_count(&pool, TestTable::Results).await, 1);
    assert_eq!(row_count(&pool, TestTable::StudentState).await, 1);
}

#[sqlx::test(migrations = "./migrations")]
async fn malformed_result_body_writes_nothing(pool: PgPool) {
    let app = test_router(pool.clone(), vec![student(42)]);
    let job_id = enqueue_job(&app, DEV_WORKER_ID, Some(42)).await;
    let (_, lease) = claim(&app, DEV_WORKER_TOKEN).await.unwrap();
    let before_results = row_count(&pool, TestTable::Results).await;
    let before_state = row_count(&pool, TestTable::StudentState).await;

    let response = send_raw_result(&app, job_id, lease, "{not valid json").await;
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    assert_eq!(row_count(&pool, TestTable::Results).await, before_results);
    assert_eq!(
        row_count(&pool, TestTable::StudentState).await,
        before_state
    );
}

#[sqlx::test(migrations = "./migrations")]
async fn oversized_result_field_writes_nothing(pool: PgPool) {
    let app = test_router(pool.clone(), vec![student(42)]);
    let job_id = enqueue_job(&app, DEV_WORKER_ID, Some(42)).await;
    let (_, lease) = claim(&app, DEV_WORKER_TOKEN).await.unwrap();
    let before_results = row_count(&pool, TestTable::Results).await;
    let before_state = row_count(&pool, TestTable::StudentState).await;

    let mut too_deep = json!("leaf");
    for _ in 0..10 {
        too_deep = json!({"safe": too_deep});
    }
    let invalid_fields = [
        json!("x".repeat(4_097)),
        Value::Array((0..1_001).map(|_| json!(0)).collect()),
        too_deep,
    ];
    for parsed_grades in invalid_fields {
        let response = post_result(
            &app,
            job_id,
            DEV_WORKER_TOKEN,
            lease,
            json!({
                "crmstudentid": 42,
                "idempotency_key": Uuid::new_v4(),
                "status": "synced",
                "parsed_grades": parsed_grades,
            }),
        )
        .await;
        assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    }
    assert_eq!(row_count(&pool, TestTable::Results).await, before_results);
    assert_eq!(
        row_count(&pool, TestTable::StudentState).await,
        before_state
    );
}

#[sqlx::test(migrations = "./migrations")]
async fn unknown_route_returns_404(pool: PgPool) {
    let before_results = row_count(&pool, TestTable::Results).await;
    let before_state = row_count(&pool, TestTable::StudentState).await;
    let response = test_router(pool.clone(), Vec::new())
        .oneshot(
            Request::builder()
                .uri("/not-a-real-route")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::NOT_FOUND);
    assert_eq!(row_count(&pool, TestTable::Results).await, before_results);
    assert_eq!(
        row_count(&pool, TestTable::StudentState).await,
        before_state
    );
}

#[sqlx::test(migrations = "./migrations")]
async fn operator_retarget_updates_one_queued_job_and_audits_reason(pool: PgPool) {
    let app = test_router(pool.clone(), vec![student(42)]);
    let job_id = enqueue_job(&app, DEV_WORKER_ID, Some(42)).await;

    let response = send(
        &app,
        Method::POST,
        &format!("/api/operator/jobs/{job_id}/retarget"),
        Some(OPERATOR_TOKEN),
        None,
        Some(json!({
            "target_worker_id": PROD_WORKER_ID,
            "reason": "  Local runner unavailable  "
        })),
    )
    .await;
    assert_eq!(response.status(), StatusCode::OK);

    let target: String =
        sqlx::query_scalar("SELECT target_worker_id FROM grade_scrape_jobs WHERE id = $1")
            .bind(job_id)
            .fetch_one(&pool)
            .await
            .unwrap();
    let audit: Value =
        sqlx::query_scalar("SELECT payload FROM grade_scrape_job_events WHERE job_id = $1")
            .bind(job_id)
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(target, PROD_WORKER_ID);
    assert_eq!(audit["operator_id"], "operator-alice");
    assert_eq!(audit["old_target_worker_id"], DEV_WORKER_ID);
    assert_eq!(audit["new_target_worker_id"], PROD_WORKER_ID);
    assert_eq!(audit["reason"], "Local runner unavailable");
    let jobs: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM grade_scrape_jobs")
        .fetch_one(&pool)
        .await
        .unwrap();
    assert_eq!(jobs, 1);
}

#[sqlx::test(migrations = "./migrations")]
async fn operator_cancel_is_terminal_and_audited(pool: PgPool) {
    let app = test_router(pool.clone(), vec![student(42)]);
    let job_id = enqueue_job(&app, DEV_WORKER_ID, Some(42)).await;

    let response = send(
        &app,
        Method::POST,
        &format!("/api/operator/jobs/{job_id}/cancel"),
        Some(OPERATOR_TOKEN),
        None,
        Some(json!({"reason": "  Test no longer required  "})),
    )
    .await;
    assert_eq!(response.status(), StatusCode::OK);

    let persisted: (String, bool) = sqlx::query_as(
        "SELECT status, completed_at IS NOT NULL FROM grade_scrape_jobs WHERE id = $1",
    )
    .bind(job_id)
    .fetch_one(&pool)
    .await
    .unwrap();
    let audit: Value =
        sqlx::query_scalar("SELECT payload FROM grade_scrape_job_events WHERE job_id = $1")
            .bind(job_id)
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(persisted, ("cancelled".into(), true));
    assert_eq!(audit["operator_id"], "operator-alice");
    assert_eq!(audit["reason"], "Test no longer required");
}

#[sqlx::test(migrations = "./migrations")]
async fn operator_cannot_retarget_or_cancel_running_job(pool: PgPool) {
    let app = test_router(pool.clone(), vec![student(42)]);
    let job_id = enqueue_job(&app, DEV_WORKER_ID, Some(42)).await;
    claim(&app, DEV_WORKER_TOKEN).await.unwrap();

    let retarget = send(
        &app,
        Method::POST,
        &format!("/api/operator/jobs/{job_id}/retarget"),
        Some(OPERATOR_TOKEN),
        None,
        Some(json!({
            "target_worker_id": PROD_WORKER_ID,
            "reason": "Already running locally"
        })),
    )
    .await;
    let cancel = send(
        &app,
        Method::POST,
        &format!("/api/operator/jobs/{job_id}/cancel"),
        Some(OPERATOR_TOKEN),
        None,
        Some(json!({"reason": "Already running locally"})),
    )
    .await;
    assert_eq!(retarget.status(), StatusCode::CONFLICT);
    assert_eq!(cancel.status(), StatusCode::CONFLICT);

    let status: String = sqlx::query_scalar("SELECT status FROM grade_scrape_jobs WHERE id = $1")
        .bind(job_id)
        .fetch_one(&pool)
        .await
        .unwrap();
    let audits: i64 =
        sqlx::query_scalar("SELECT COUNT(*) FROM grade_scrape_job_events WHERE job_id = $1")
            .bind(job_id)
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(status, "running");
    assert_eq!(audits, 0);
}

#[sqlx::test(migrations = "./migrations")]
async fn active_franchise_kind_uniqueness_is_global_across_targets(pool: PgPool) {
    let app = test_router(pool.clone(), vec![student(42)]);
    let first = enqueue(&app, DEV_WORKER_ID, Some(42), Uuid::new_v4()).await;
    assert_eq!(first.status(), StatusCode::OK);
    let second = enqueue(&app, PROD_WORKER_ID, Some(42), Uuid::new_v4()).await;
    assert_eq!(second.status(), StatusCode::CONFLICT);
    let jobs: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM grade_scrape_jobs")
        .fetch_one(&pool)
        .await
        .unwrap();
    assert_eq!(jobs, 1);
}
