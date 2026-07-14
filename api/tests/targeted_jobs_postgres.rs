use api::error::ApiError;
use api::models::{
    SchedulerJobKind, SchedulerJobRequest, WorkerResultRequest, WorkerResultStateAction,
};
use api::queries::{claim_next_job, create_scheduler_job, record_worker_result};
use sqlx::PgPool;
use uuid::Uuid;

async fn insert_running_job(pool: &PgPool, worker_id: &str, lease_token: Uuid) -> Uuid {
    sqlx::query_scalar(
        r#"
        INSERT INTO grade_scrape_jobs (
            franchise_id,
            kind,
            status,
            target_worker_id,
            worker_id,
            lease_token,
            lease_expires_at,
            attempt_count
        )
        VALUES (11, 'grade', 'running', $1, $1, $2, NOW() + INTERVAL '5 minutes', 1)
        RETURNING id
        "#,
    )
    .bind(worker_id)
    .bind(lease_token)
    .fetch_one(pool)
    .await
    .unwrap()
}

fn result_request(student_id: i64, idempotency_key: Uuid, status: &str) -> WorkerResultRequest {
    serde_json::from_value(serde_json::json!({
        "crmstudentid": student_id,
        "idempotency_key": idempotency_key,
        "status": status,
    }))
    .unwrap()
}

#[sqlx::test(migrations = "./migrations")]
async fn targeted_worker_is_the_only_worker_that_can_claim(pool: PgPool) {
    let job_id: Uuid = sqlx::query_scalar(
        "INSERT INTO grade_scrape_jobs (franchise_id, kind, target_worker_id) VALUES (11, 'grade', 'dev-alice-laptop') RETURNING id"
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    assert!(claim_next_job(&pool, "prod-windows-01", 300)
        .await
        .unwrap()
        .is_none());
    let claim = claim_next_job(&pool, "dev-alice-laptop", 300)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(claim.job_id, job_id);
}

#[sqlx::test(migrations = "./migrations")]
async fn active_job_uniqueness_survives_different_targets(pool: PgPool) {
    sqlx::query(
        "INSERT INTO grade_scrape_jobs (franchise_id, kind, target_worker_id) VALUES (11, 'grade', 'worker-a')",
    )
    .execute(&pool)
    .await
    .unwrap();

    let error = sqlx::query(
        "INSERT INTO grade_scrape_jobs (franchise_id, kind, target_worker_id) VALUES (11, 'grade', 'worker-b')",
    )
    .execute(&pool)
    .await
    .unwrap_err();
    let database = error.as_database_error().unwrap();
    assert_eq!(database.constraint(), Some("uq_grade_scrape_jobs_active"));
}

#[sqlx::test(migrations = "./migrations")]
async fn changed_target_with_reused_scheduler_key_is_conflict(pool: PgPool) {
    let idempotency_key = Uuid::new_v4();
    let first = SchedulerJobRequest {
        idempotency_key,
        kind: SchedulerJobKind::Grade,
        franchise_id: 11,
        student_id: Some(42),
        target_worker_id: "worker-a".into(),
    };
    create_scheduler_job(&pool, "scheduler-a", &first)
        .await
        .unwrap();

    let changed = SchedulerJobRequest {
        target_worker_id: "worker-b".into(),
        ..first
    };
    assert!(matches!(
        create_scheduler_job(&pool, "scheduler-a", &changed).await,
        Err(ApiError::Conflict(_))
    ));
}

#[sqlx::test(migrations = "./migrations")]
async fn expired_lease_is_rejected_for_result_write(pool: PgPool) {
    let lease_token = Uuid::new_v4();
    let job_id: Uuid = sqlx::query_scalar(
        r#"
        INSERT INTO grade_scrape_jobs (
            franchise_id, kind, status, target_worker_id, worker_id,
            lease_token, lease_expires_at, attempt_count
        )
        VALUES (11, 'grade', 'running', 'worker-a', 'worker-a', $1, NOW() - INTERVAL '1 second', 1)
        RETURNING id
        "#,
    )
    .bind(lease_token)
    .fetch_one(&pool)
    .await
    .unwrap();
    let request = result_request(42, Uuid::new_v4(), "synced");

    assert!(matches!(
        record_worker_result(
            &pool,
            job_id,
            "worker-a",
            lease_token,
            &request,
            WorkerResultStateAction::RecordOnly,
        )
        .await,
        Err(ApiError::NotFound)
    ));
    let count: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM grade_scrape_results")
        .fetch_one(&pool)
        .await
        .unwrap();
    assert_eq!(count, 0);
}

#[sqlx::test(migrations = "./migrations")]
async fn duplicate_result_uuid_is_idempotent_but_changed_payload_conflicts(pool: PgPool) {
    let lease_token = Uuid::new_v4();
    let job_id = insert_running_job(&pool, "worker-a", lease_token).await;
    let idempotency_key = Uuid::new_v4();
    let original = result_request(42, idempotency_key, "synced");

    for _ in 0..2 {
        record_worker_result(
            &pool,
            job_id,
            "worker-a",
            lease_token,
            &original,
            WorkerResultStateAction::RecordOnly,
        )
        .await
        .unwrap();
    }

    let changed = result_request(42, idempotency_key, "bad_login");
    assert!(matches!(
        record_worker_result(
            &pool,
            job_id,
            "worker-a",
            lease_token,
            &changed,
            WorkerResultStateAction::RecordOnly,
        )
        .await,
        Err(ApiError::Conflict(_))
    ));
    let count: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM grade_scrape_results")
        .fetch_one(&pool)
        .await
        .unwrap();
    assert_eq!(count, 1);
}

#[sqlx::test(migrations = "./migrations")]
async fn duplicate_result_uuid_for_different_student_conflicts_even_with_same_content(
    pool: PgPool,
) {
    let lease_token = Uuid::new_v4();
    let job_id = insert_running_job(&pool, "worker-a", lease_token).await;
    let idempotency_key = Uuid::new_v4();
    let first = result_request(41, idempotency_key, "synced");
    record_worker_result(
        &pool,
        job_id,
        "worker-a",
        lease_token,
        &first,
        WorkerResultStateAction::RecordOnly,
    )
    .await
    .unwrap();

    let changed_student = result_request(42, idempotency_key, "synced");
    assert!(matches!(
        record_worker_result(
            &pool,
            job_id,
            "worker-a",
            lease_token,
            &changed_student,
            WorkerResultStateAction::RecordOnly,
        )
        .await,
        Err(ApiError::Conflict(_))
    ));
    let count: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM grade_scrape_results")
        .fetch_one(&pool)
        .await
        .unwrap();
    assert_eq!(count, 1);
}
