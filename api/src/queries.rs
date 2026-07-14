use std::collections::HashMap;

use chrono::{DateTime, Utc};
use sqlx::{query, query_as, query_scalar, PgPool, Postgres, Transaction};
use uuid::Uuid;

use crate::credentials::EncryptedCredentialEnvelope;
use crate::error::ApiError;
use crate::models::{
    validated_operator_reason, JobEvent, JobResult, ManualPullRequest, ManualPullResponse,
    SchedulerJobRequest, StudentGradeState, WorkerClaimResponse, WorkerCompletionRequest,
    WorkerEventRequest, WorkerFailRequest, WorkerHeartbeatRequest, WorkerJob, WorkerResultRequest,
    WorkerResultStateAction,
};

const QUERY_GRADES_TABLE_EXISTS: &str =
    "SELECT to_regclass('public.students_grades_20262027') IS NOT NULL";
const QUERY_CLAIM_DASHBOARD_NONCE: &str = r#"
INSERT INTO dashboard_replay_nonces (identity_hash, nonce, expires_at)
VALUES ($1, $2, $3)
ON CONFLICT (identity_hash, nonce) DO UPDATE
SET expires_at = EXCLUDED.expires_at,
    claimed_at = now()
WHERE dashboard_replay_nonces.expires_at < now()
RETURNING 1
"#;
const QUERY_CLEANUP_DASHBOARD_NONCES: &str = r#"
WITH expired AS (
    SELECT ctid
    FROM dashboard_replay_nonces
    WHERE expires_at < now()
    ORDER BY expires_at
    LIMIT $1
)
DELETE FROM dashboard_replay_nonces AS nonces
USING expired
WHERE nonces.ctid = expired.ctid
"#;

const QUERY_STATE_BY_CRM_IDS: &str = r#"
SELECT
    uuid,
    crmstudentid,
    portal2,
    p2username,
    p2password,
    alternate_credentials_version,
    alternate_credentials_key_id,
    alternate_credentials_nonce,
    alternate_credentials_ciphertext,
    yearstart,
    yearend,
    weeklydata,
    portal,
    passwordgood,
    status,
    error_msg,
    track_agenda,
    weekly_agenda,
    created_at,
    updated_at
FROM students_grades_20262027
WHERE crmstudentid = ANY($1)
"#;

pub async fn claim_dashboard_nonce(
    neon_db: &PgPool,
    identity_hash: &[u8; 32],
    nonce: Uuid,
    expires_at: DateTime<Utc>,
) -> Result<bool, ApiError> {
    let claimed = query_scalar::<_, i32>(QUERY_CLAIM_DASHBOARD_NONCE)
        .bind(identity_hash.as_slice())
        .bind(nonce)
        .bind(expires_at)
        .fetch_optional(neon_db)
        .await?;
    Ok(claimed.is_some())
}

pub async fn cleanup_expired_dashboard_nonces(
    neon_db: &PgPool,
    batch_size: i64,
) -> Result<u64, ApiError> {
    let result = query(QUERY_CLEANUP_DASHBOARD_NONCES)
        .bind(batch_size.clamp(1, 10_000))
        .execute(neon_db)
        .await?;
    Ok(result.rows_affected())
}

const QUERY_RECONCILE_CREATE_STATE: &str = r#"
INSERT INTO students_grades_20262027 (crmstudentid)
SELECT student_id::integer
FROM UNNEST($1::bigint[]) AS student_id
ON CONFLICT (crmstudentid) DO NOTHING
"#;

const QUERY_ENSURE_STATE: &str = r#"
INSERT INTO students_grades_20262027 (crmstudentid)
VALUES ($1)
ON CONFLICT (crmstudentid) DO NOTHING
"#;

const QUERY_WRITE_ALTERNATE_CREDENTIALS: &str = r#"
INSERT INTO students_grades_20262027 (
    crmstudentid,
    portal2,
    p2username,
    p2password,
    alternate_credentials_version,
    alternate_credentials_key_id,
    alternate_credentials_nonce,
    alternate_credentials_ciphertext
)
VALUES ($1, $2, NULL, NULL, $3, $4, $5, $6)
ON CONFLICT (crmstudentid) DO UPDATE
SET portal2 = EXCLUDED.portal2,
    p2username = NULL,
    p2password = NULL,
    alternate_credentials_version = EXCLUDED.alternate_credentials_version,
    alternate_credentials_key_id = EXCLUDED.alternate_credentials_key_id,
    alternate_credentials_nonce = EXCLUDED.alternate_credentials_nonce,
    alternate_credentials_ciphertext = EXCLUDED.alternate_credentials_ciphertext
"#;

const QUERY_CLEAR_ALTERNATE_CREDENTIALS: &str = r#"
UPDATE students_grades_20262027
SET portal2 = NULL,
    p2username = NULL,
    p2password = NULL,
    alternate_credentials_version = NULL,
    alternate_credentials_key_id = NULL,
    alternate_credentials_nonce = NULL,
    alternate_credentials_ciphertext = NULL,
    track_agenda = FALSE
WHERE crmstudentid = $1
"#;

pub async fn write_alternate_credentials(
    neon_db: &PgPool,
    crmstudentid: i64,
    portal_url: &str,
    envelope: &EncryptedCredentialEnvelope,
) -> Result<(), ApiError> {
    query(QUERY_WRITE_ALTERNATE_CREDENTIALS)
        .bind(crmstudentid)
        .bind(portal_url)
        .bind(envelope.version)
        .bind(&envelope.key_id)
        .bind(&envelope.nonce)
        .bind(&envelope.ciphertext)
        .execute(neon_db)
        .await?;
    Ok(())
}

pub async fn clear_alternate_credentials(
    neon_db: &PgPool,
    crmstudentid: i64,
) -> Result<bool, ApiError> {
    let result = query(QUERY_CLEAR_ALTERNATE_CREDENTIALS)
        .bind(crmstudentid)
        .execute(neon_db)
        .await?;
    Ok(result.rows_affected() == 1)
}

const QUERY_RECONCILE_DELETE_STATE: &str = r#"
DELETE FROM students_grades_20262027
WHERE NOT (crmstudentid::bigint = ANY($1::bigint[]))
"#;

const QUERY_ACTIVE_JOB_EXISTS: &str = r#"
SELECT id
FROM grade_scrape_jobs
WHERE franchise_id = $1
  AND kind = $2
  AND status IN ('queued', 'running')
LIMIT 1
"#;

const QUERY_COOLDOWN_JOB_EXISTS: &str = r#"
SELECT id
FROM grade_scrape_jobs
WHERE franchise_id = $1
  AND kind = $2
  AND created_at >= NOW() - ($3::text)::interval
ORDER BY created_at DESC
LIMIT 1
"#;

const QUERY_CREATE_JOB: &str = r#"
INSERT INTO grade_scrape_jobs (
    kind,
    franchise_id,
    student_id,
    target_worker_id,
    status,
    created_by_user,
    created_by_role,
    created_by_franchise_id,
    payload
) VALUES ($1, $2, $3, $4, 'queued', $5, $6, $7, $8::jsonb)
RETURNING id
"#;

const QUERY_FIND_SCHEDULER_JOB: &str = r#"
SELECT id, scheduler_request_hash
FROM grade_scrape_jobs
WHERE scheduler_identity = $1
  AND scheduler_idempotency_key = $2
"#;

const QUERY_CREATE_SCHEDULER_JOB: &str = r#"
INSERT INTO grade_scrape_jobs (
    kind,
    franchise_id,
    student_id,
    target_worker_id,
    status,
    created_by_user,
    created_by_franchise_id,
    scheduler_identity,
    scheduler_idempotency_key,
    scheduler_request_hash,
    payload
) VALUES ($1, $2, $3, $4, 'queued', $5, $2, $5, $6, $7, '{}'::jsonb)
ON CONFLICT (scheduler_identity, scheduler_idempotency_key)
    WHERE scheduler_identity IS NOT NULL
      AND scheduler_idempotency_key IS NOT NULL
DO NOTHING
RETURNING id
"#;

const QUERY_CLAIM_NEXT_JOB: &str = r#"
WITH cleanup_candidate AS (
    SELECT id
    FROM grade_scrape_jobs
    WHERE target_worker_id = $1
      AND status = 'running'
      AND COALESCE(attempt_count, 0) >= 3
      AND (lease_expires_at IS NULL OR lease_expires_at <= NOW())
    ORDER BY created_at
    FOR UPDATE SKIP LOCKED
    LIMIT 1
),
expired_exhausted AS (
    UPDATE grade_scrape_jobs AS jobs
    SET
        status = 'failed',
        error_msg = 'Worker lease expired after maximum attempts.',
        completed_at = NOW(),
        updated_at = NOW()
    FROM cleanup_candidate
    WHERE jobs.id = cleanup_candidate.id
    RETURNING jobs.id
),
candidate AS (
    SELECT jobs.id
    FROM grade_scrape_jobs AS jobs
    WHERE jobs.target_worker_id = $1
      AND (
          jobs.status = 'queued'
          OR (
              jobs.status = 'running'
              AND (jobs.lease_expires_at IS NULL OR jobs.lease_expires_at <= NOW())
              AND COALESCE(jobs.attempt_count, 0) < 3
          )
      )
      AND NOT EXISTS (
          SELECT 1
          FROM expired_exhausted
          WHERE expired_exhausted.id = jobs.id
      )
    ORDER BY jobs.created_at
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
UPDATE grade_scrape_jobs AS jobs
SET
    status = 'running',
    worker_id = $1,
    lease_token = $2,
    lease_expires_at = NOW() + ($3::int * INTERVAL '1 second'),
    attempt_count = LEAST(COALESCE(jobs.attempt_count, 0) + 1, 3),
    heartbeat = NULL,
    started_at = NOW(),
    updated_at = NOW()
FROM candidate
WHERE jobs.id = candidate.id
RETURNING
    jobs.id AS job_id,
    jobs.kind,
    jobs.franchise_id,
    jobs.student_id,
    jobs.lease_token,
    jobs.lease_expires_at
"#;

const QUERY_LIST_CURRENT_JOBS: &str = r#"
SELECT
    id,
    kind,
    status,
    franchise_id,
    student_id,
    target_worker_id,
    worker_id,
    heartbeat,
    completed_payload,
    created_at,
    updated_at,
    started_at,
    completed_at
FROM grade_scrape_jobs
WHERE status IN ('queued', 'running')
  AND ($1::int IS NULL OR franchise_id = $1)
ORDER BY created_at DESC
"#;

const QUERY_JOB_BY_ID: &str = r#"
SELECT
    id,
    kind,
    status,
    franchise_id,
    student_id,
    target_worker_id,
    worker_id,
    heartbeat,
    completed_payload,
    created_at,
    updated_at,
    started_at,
    completed_at
FROM grade_scrape_jobs
WHERE id = $1
  AND ($2::int IS NULL OR franchise_id = $2)
"#;

const QUERY_LOCK_JOB_STATUS: &str = r#"
SELECT status, target_worker_id
FROM grade_scrape_jobs
WHERE id = $1
FOR UPDATE
"#;

const QUERY_RETARGET_QUEUED_JOB: &str = r#"
UPDATE grade_scrape_jobs
SET target_worker_id = $2, updated_at = NOW()
WHERE id = $1 AND status = 'queued'
RETURNING
    id,
    kind,
    status,
    franchise_id,
    student_id,
    target_worker_id,
    worker_id,
    heartbeat,
    completed_payload,
    created_at,
    updated_at,
    started_at,
    completed_at
"#;

const QUERY_CANCEL_QUEUED_JOB: &str = r#"
UPDATE grade_scrape_jobs
SET status = 'cancelled', completed_at = NOW(), updated_at = NOW()
WHERE id = $1 AND status = 'queued'
RETURNING
    id,
    kind,
    status,
    franchise_id,
    student_id,
    target_worker_id,
    worker_id,
    heartbeat,
    completed_payload,
    created_at,
    updated_at,
    started_at,
    completed_at
"#;

const QUERY_INSERT_OPERATOR_AUDIT: &str = r#"
INSERT INTO grade_scrape_job_events (job_id, level, message, payload)
VALUES ($1, 'info', $2, $3::jsonb)
"#;

const QUERY_RUNNING_JOB_FOR_WORKER: &str = r#"
SELECT
    id,
    kind,
    status,
    franchise_id,
    student_id,
    target_worker_id,
    worker_id,
    heartbeat,
    completed_payload,
    created_at,
    updated_at,
    started_at,
    completed_at
FROM grade_scrape_jobs
WHERE id = $1
  AND worker_id = $2
  AND lease_token = $3
  AND status = 'running'
  AND lease_expires_at > NOW()
"#;

const QUERY_HEARTBEAT: &str = r#"
UPDATE grade_scrape_jobs
SET
    heartbeat = $2::jsonb,
    lease_expires_at = NOW() + ($5::int * INTERVAL '1 second'),
    updated_at = NOW()
WHERE id = $1
  AND worker_id = $3
  AND lease_token = $4
  AND status = 'running'
  AND lease_expires_at > NOW()
"#;

const QUERY_INSERT_EVENT: &str = r#"
WITH active_worker_lease AS (
    SELECT id
    FROM grade_scrape_jobs
    WHERE id = $1
      AND worker_id = $5
      AND lease_token = $6
      AND status = 'running'
      AND lease_expires_at > NOW()
    FOR UPDATE
)
INSERT INTO grade_scrape_job_events (job_id, level, message, payload)
SELECT $1, $2, $3, $4
FROM active_worker_lease
"#;

const QUERY_LIST_EVENTS: &str = r#"
SELECT id, job_id, level, message, payload, created_at
FROM grade_scrape_job_events
WHERE job_id = $1
ORDER BY created_at, id
"#;

const QUERY_COMPLETE: &str = r#"
UPDATE grade_scrape_jobs
SET status = 'complete', completed_at = NOW(), completed_payload = $2::jsonb, updated_at = NOW()
WHERE id = $1 AND worker_id = $3 AND status = 'running'
  AND lease_token = $4
  AND lease_expires_at > NOW()
"#;

const QUERY_FAIL: &str = r#"
UPDATE grade_scrape_jobs
SET status = 'failed', error_msg = $2, completed_at = NOW(), updated_at = NOW()
WHERE id = $1
  AND worker_id = $3
  AND lease_token = $4
  AND status = 'running'
  AND lease_expires_at > NOW()
"#;

const QUERY_INSERT_RESULT: &str = r#"
INSERT INTO grade_scrape_results (job_id, crmstudentid, idempotency_key, payload)
SELECT $1, $2, $3, $4
WHERE EXISTS (
    SELECT 1
    FROM grade_scrape_jobs
    WHERE id = $1
      AND worker_id = $5
      AND lease_token = $6
      AND status = 'running'
      AND lease_expires_at > NOW()
)
ON CONFLICT (job_id, idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING
RETURNING id
"#;

const QUERY_LOCK_ACTIVE_LEASE: &str = r#"
SELECT id
FROM grade_scrape_jobs
WHERE id = $1
  AND worker_id = $2
  AND lease_token = $3
  AND status = 'running'
  AND lease_expires_at > NOW()
FOR UPDATE
"#;

const QUERY_EXISTING_RESULT_PAYLOAD: &str = r#"
SELECT crmstudentid, payload
FROM grade_scrape_results
WHERE job_id = $1 AND idempotency_key = $2
"#;

const QUERY_APPLY_RESULT_STATE: &str = r#"
UPDATE students_grades_20262027
SET
    weeklydata = CASE
        WHEN $2::jsonb IS NULL THEN weeklydata
        ELSE COALESCE(weeklydata, '{}'::jsonb)
            || jsonb_build_object(to_char(date_trunc('week', now())::date, 'YYYY-MM-DD'), $2::jsonb)
    END,
    weekly_agenda = COALESCE($3::jsonb, weekly_agenda),
    status = COALESCE($4, status),
    passwordgood = COALESCE($5, passwordgood),
    error_msg = CASE
        WHEN $6::text IS NOT NULL THEN $6
        WHEN $4 = 'synced' THEN NULL
        ELSE error_msg
    END,
    updated_at = NOW()
WHERE crmstudentid = $1
"#;

const QUERY_LATEST_RESULTS: &str = r#"
SELECT r.id, r.job_id, r.crmstudentid, r.payload, r.created_at
FROM grade_scrape_results r
JOIN grade_scrape_jobs j ON j.id = r.job_id
WHERE ($1::int IS NULL OR j.franchise_id = $1)
ORDER BY r.created_at DESC, r.id DESC
LIMIT $2
"#;

pub async fn has_grades_table(neon_db: &PgPool) -> Result<bool, ApiError> {
    let has_table: bool = query_scalar(QUERY_GRADES_TABLE_EXISTS)
        .fetch_one(neon_db)
        .await?;
    Ok(has_table)
}

pub async fn ensure_grades_table(neon_db: &PgPool) -> Result<(), ApiError> {
    if !has_grades_table(neon_db).await? {
        return Err(ApiError::Safe(
            "students_grades_20262027 table does not exist".into(),
        ));
    }
    Ok(())
}

pub async fn states_by_crm_ids(
    neon_db: &PgPool,
    ids: &[i64],
) -> Result<HashMap<i64, StudentGradeState>, ApiError> {
    if ids.is_empty() {
        return Ok(HashMap::new());
    }
    let rows = query_as::<_, StudentGradeState>(QUERY_STATE_BY_CRM_IDS)
        .bind(ids)
        .fetch_all(neon_db)
        .await?;
    Ok(rows
        .into_iter()
        .map(|row| (row.crmstudentid, row))
        .collect())
}

pub async fn reconcile_student_state(
    neon_db: &PgPool,
    eligible_ids: &[i64],
) -> Result<(u64, u64), ApiError> {
    if eligible_ids
        .iter()
        .any(|value| *value <= 0 || *value > i64::from(i32::MAX))
    {
        return Err(ApiError::Safe(
            "CRM returned an invalid student identifier".into(),
        ));
    }
    let mut tx = neon_db.begin().await?;
    let created = query(QUERY_RECONCILE_CREATE_STATE)
        .bind(eligible_ids)
        .execute(&mut *tx)
        .await?
        .rows_affected();
    let deleted = query(QUERY_RECONCILE_DELETE_STATE)
        .bind(eligible_ids)
        .execute(&mut *tx)
        .await?
        .rows_affected();
    tx.commit().await?;
    Ok((created, deleted))
}

async fn job_exists(
    tx: &mut Transaction<'_, Postgres>,
    query_text: &str,
    franchise_id: i32,
    kind: &str,
    cooldown_interval: Option<&str>,
) -> Result<Option<Uuid>, ApiError> {
    let mut query = query_scalar::<_, Uuid>(query_text)
        .bind(franchise_id)
        .bind(kind.to_string());
    if let Some(interval) = cooldown_interval {
        query = query.bind(interval.to_string());
    }
    Ok(query.fetch_optional(&mut **tx).await?)
}

pub async fn create_manual_pull_job(
    neon_db: &PgPool,
    payload: &ManualPullRequest,
    franchise_id: i32,
    role: Option<i32>,
    user: Option<&str>,
    target_worker_id: &str,
) -> Result<ManualPullResponse, ApiError> {
    let kind = payload.kind.as_deref().unwrap_or("grade").trim();
    if kind.is_empty() || !matches!(kind, "grade" | "agenda") {
        return Err(ApiError::BadRequest("Unsupported job kind".into()));
    }

    let mut tx = neon_db.begin().await?;
    if job_exists(&mut tx, QUERY_ACTIVE_JOB_EXISTS, franchise_id, kind, None)
        .await?
        .is_some()
    {
        return Err(ApiError::Conflict(
            "A scrape is already queued or running for this center".into(),
        ));
    }
    if job_exists(
        &mut tx,
        QUERY_COOLDOWN_JOB_EXISTS,
        franchise_id,
        kind,
        Some("5 minutes"),
    )
    .await?
    .is_some()
    {
        return Err(ApiError::Conflict(
            "Manual scrape cooldown is still active for this center".into(),
        ));
    }

    let created_by = user.map(str::to_string);
    let job_id: Uuid = query_scalar(QUERY_CREATE_JOB)
        .bind(kind)
        .bind(franchise_id)
        .bind(payload.student_id)
        .bind(target_worker_id)
        .bind(created_by)
        .bind(role)
        .bind(franchise_id)
        .bind(serde_json::json!({
            "kind": kind,
            "student_id": payload.student_id,
            "franchise_id": franchise_id,
            "target_worker_id": target_worker_id,
        }))
        .fetch_one(&mut *tx)
        .await?;
    tx.commit().await?;

    Ok(ManualPullResponse {
        job_id,
        status: "queued".into(),
    })
}

async fn scheduler_job_by_key(
    neon_db: &PgPool,
    scheduler_id: &str,
    idempotency_key: Uuid,
) -> Result<Option<(Uuid, Vec<u8>)>, ApiError> {
    Ok(query_as::<_, (Uuid, Vec<u8>)>(QUERY_FIND_SCHEDULER_JOB)
        .bind(scheduler_id)
        .bind(idempotency_key)
        .fetch_optional(neon_db)
        .await?)
}

pub async fn create_scheduler_job(
    neon_db: &PgPool,
    scheduler_id: &str,
    payload: &SchedulerJobRequest,
) -> Result<WorkerJob, ApiError> {
    payload.validate()?;
    let request_hash = payload.request_hash();

    let job_id = match scheduler_job_by_key(neon_db, scheduler_id, payload.idempotency_key).await? {
        Some((job_id, existing_hash)) => {
            if existing_hash.as_slice() != request_hash {
                return Err(ApiError::Conflict(
                    "Idempotency key was already used for a different scheduler request".into(),
                ));
            }
            job_id
        }
        None => {
            let inserted = query_scalar::<_, Uuid>(QUERY_CREATE_SCHEDULER_JOB)
                .bind(payload.kind.as_str())
                .bind(payload.franchise_id)
                .bind(payload.student_id)
                .bind(&payload.target_worker_id)
                .bind(scheduler_id)
                .bind(payload.idempotency_key)
                .bind(request_hash.as_slice())
                .fetch_optional(neon_db)
                .await;
            match inserted {
                Ok(Some(job_id)) => job_id,
                Ok(None) => {
                    let Some((job_id, existing_hash)) =
                        scheduler_job_by_key(neon_db, scheduler_id, payload.idempotency_key)
                            .await?
                    else {
                        return Err(ApiError::Unavailable);
                    };
                    if existing_hash.as_slice() != request_hash {
                        return Err(ApiError::Conflict(
                            "Idempotency key was already used for a different scheduler request"
                                .into(),
                        ));
                    }
                    job_id
                }
                Err(sqlx::Error::Database(error))
                    if error.constraint() == Some("uq_grade_scrape_jobs_active") =>
                {
                    return Err(ApiError::Conflict(
                        "A scrape is already queued or running for this center".into(),
                    ));
                }
                Err(error) => return Err(ApiError::Db(error)),
            }
        }
    };

    get_job(neon_db, job_id, None)
        .await?
        .ok_or(ApiError::Unavailable)
}

pub async fn claim_next_job(
    neon_db: &PgPool,
    worker_id: &str,
    lease_seconds: i64,
) -> Result<Option<WorkerClaimResponse>, ApiError> {
    Ok(query_as::<_, WorkerClaimResponse>(QUERY_CLAIM_NEXT_JOB)
        .bind(worker_id)
        .bind(Uuid::new_v4())
        .bind(lease_seconds)
        .fetch_optional(neon_db)
        .await?)
}

pub async fn list_current_jobs(
    neon_db: &PgPool,
    franchise_id: Option<i32>,
) -> Result<Vec<WorkerJob>, ApiError> {
    Ok(query_as::<_, WorkerJob>(QUERY_LIST_CURRENT_JOBS)
        .bind(franchise_id)
        .fetch_all(neon_db)
        .await?)
}

pub async fn get_job(
    neon_db: &PgPool,
    job_id: Uuid,
    franchise_id: Option<i32>,
) -> Result<Option<WorkerJob>, ApiError> {
    Ok(query_as::<_, WorkerJob>(QUERY_JOB_BY_ID)
        .bind(job_id)
        .bind(franchise_id)
        .fetch_optional(neon_db)
        .await?)
}

async fn require_queued_job(
    tx: &mut Transaction<'_, Postgres>,
    job_id: Uuid,
) -> Result<String, ApiError> {
    match query_as::<_, (String, Option<String>)>(QUERY_LOCK_JOB_STATUS)
        .bind(job_id)
        .fetch_optional(&mut **tx)
        .await?
    {
        None => Err(ApiError::NotFound),
        Some((status, target_worker_id)) if status == "queued" => {
            target_worker_id.ok_or(ApiError::Unavailable)
        }
        Some(_) => Err(ApiError::Conflict(
            "Only queued jobs may be changed by an operator".into(),
        )),
    }
}

pub async fn retarget_queued_job(
    neon_db: &PgPool,
    job_id: Uuid,
    target_worker_id: &str,
    operator_id: &str,
    reason: &str,
) -> Result<WorkerJob, ApiError> {
    if target_worker_id.is_empty() || target_worker_id.trim() != target_worker_id {
        return Err(ApiError::BadRequest(
            "Target worker must be a valid identifier".into(),
        ));
    }
    let reason = validated_operator_reason(reason)?;
    let mut tx = neon_db.begin().await?;
    let old_target_worker_id = require_queued_job(&mut tx, job_id).await?;
    let job = query_as::<_, WorkerJob>(QUERY_RETARGET_QUEUED_JOB)
        .bind(job_id)
        .bind(target_worker_id)
        .fetch_optional(&mut *tx)
        .await?
        .ok_or(ApiError::Unavailable)?;
    query(QUERY_INSERT_OPERATOR_AUDIT)
        .bind(job_id)
        .bind("Operator retargeted queued job")
        .bind(serde_json::json!({
            "operator_id": operator_id,
            "old_target_worker_id": old_target_worker_id,
            "new_target_worker_id": target_worker_id,
            "reason": reason,
        }))
        .execute(&mut *tx)
        .await?;
    tx.commit().await?;
    Ok(job)
}

pub async fn cancel_queued_job(
    neon_db: &PgPool,
    job_id: Uuid,
    operator_id: &str,
    reason: &str,
) -> Result<WorkerJob, ApiError> {
    let reason = validated_operator_reason(reason)?;
    let mut tx = neon_db.begin().await?;
    require_queued_job(&mut tx, job_id).await?;
    let job = query_as::<_, WorkerJob>(QUERY_CANCEL_QUEUED_JOB)
        .bind(job_id)
        .fetch_optional(&mut *tx)
        .await?
        .ok_or(ApiError::Unavailable)?;
    query(QUERY_INSERT_OPERATOR_AUDIT)
        .bind(job_id)
        .bind("Operator cancelled queued job")
        .bind(serde_json::json!({
            "operator_id": operator_id,
            "reason": reason,
        }))
        .execute(&mut *tx)
        .await?;
    tx.commit().await?;
    Ok(job)
}

pub async fn get_running_job_for_worker(
    neon_db: &PgPool,
    job_id: Uuid,
    worker_id: &str,
    lease_token: Uuid,
) -> Result<Option<WorkerJob>, ApiError> {
    Ok(query_as::<_, WorkerJob>(QUERY_RUNNING_JOB_FOR_WORKER)
        .bind(job_id)
        .bind(worker_id)
        .bind(lease_token)
        .fetch_optional(neon_db)
        .await?)
}

pub async fn update_heartbeat(
    neon_db: &PgPool,
    job_id: Uuid,
    worker_id: &str,
    lease_token: Uuid,
    lease_seconds: i64,
    payload: &WorkerHeartbeatRequest,
) -> Result<(), ApiError> {
    let heartbeat = serde_json::to_value(payload)
        .map_err(|_| ApiError::Safe("Unable to encode worker heartbeat".into()))?;
    let update_result = query(QUERY_HEARTBEAT)
        .bind(job_id)
        .bind(heartbeat)
        .bind(worker_id)
        .bind(lease_token)
        .bind(lease_seconds)
        .execute(neon_db)
        .await?;
    require_job_mutation(update_result.rows_affected())
}

pub async fn create_event(
    neon_db: &PgPool,
    job_id: Uuid,
    worker_id: &str,
    lease_token: Uuid,
    payload: &WorkerEventRequest,
) -> Result<(), ApiError> {
    let insert_result = query(QUERY_INSERT_EVENT)
        .bind(job_id)
        .bind(payload.storage_level())
        .bind(payload.storage_message())
        .bind(payload.storage_payload())
        .bind(worker_id)
        .bind(lease_token)
        .execute(neon_db)
        .await?;
    require_job_mutation(insert_result.rows_affected())
}

pub async fn list_events(neon_db: &PgPool, job_id: Uuid) -> Result<Vec<JobEvent>, ApiError> {
    Ok(query_as::<_, JobEvent>(QUERY_LIST_EVENTS)
        .bind(job_id)
        .fetch_all(neon_db)
        .await?)
}

pub async fn record_worker_result(
    neon_db: &PgPool,
    job_id: Uuid,
    worker_id: &str,
    lease_token: Uuid,
    payload: &WorkerResultRequest,
    state_action: WorkerResultStateAction,
) -> Result<(), ApiError> {
    let mut tx = neon_db.begin().await?;
    let locked: Option<Uuid> = query_scalar(QUERY_LOCK_ACTIVE_LEASE)
        .bind(job_id)
        .bind(worker_id)
        .bind(lease_token)
        .fetch_optional(&mut *tx)
        .await?;
    if locked.is_none() {
        return Err(ApiError::NotFound);
    }
    let canonical_payload = payload.storage_payload();
    let inserted: Option<i64> = query_scalar(QUERY_INSERT_RESULT)
        .bind(job_id)
        .bind(payload.crmstudentid())
        .bind(payload.idempotency_key)
        .bind(canonical_payload.clone())
        .bind(worker_id)
        .bind(lease_token)
        .fetch_optional(&mut *tx)
        .await?;

    if inserted.is_none() {
        let existing: Option<(Option<i64>, serde_json::Value)> =
            query_as(QUERY_EXISTING_RESULT_PAYLOAD)
                .bind(job_id)
                .bind(payload.idempotency_key)
                .fetch_optional(&mut *tx)
                .await?;
        let (existing_student_id, existing_payload) = existing.ok_or(ApiError::NotFound)?;
        let existing_student_id = existing_student_id.ok_or_else(|| {
            ApiError::Conflict(
                "Idempotency key was already used for a result without a student identity".into(),
            )
        })?;
        duplicate_result_identity_decision(
            existing_student_id,
            &existing_payload,
            payload.crmstudentid(),
            &canonical_payload,
        )?;
        tx.commit().await?;
        return Ok(());
    }

    if let WorkerResultStateAction::ApplyStudentState(student_id) = state_action {
        ensure_state_for_student_tx(&mut tx, student_id).await?;
        apply_result_state_tx(&mut tx, student_id, payload).await?;
    }
    tx.commit().await?;
    Ok(())
}

async fn ensure_state_for_student_tx(
    tx: &mut Transaction<'_, Postgres>,
    crmstudentid: i64,
) -> Result<(), ApiError> {
    query(QUERY_ENSURE_STATE)
        .bind(crmstudentid)
        .execute(&mut **tx)
        .await?;
    Ok(())
}

async fn apply_result_state_tx(
    tx: &mut Transaction<'_, Postgres>,
    crmstudentid: i64,
    payload: &WorkerResultRequest,
) -> Result<(), ApiError> {
    let update_result = query(QUERY_APPLY_RESULT_STATE)
        .bind(crmstudentid)
        .bind(payload.parsed_grades.clone())
        .bind(payload.weekly_agenda.clone())
        .bind(Some(payload.storage_status()))
        .bind(payload.passwordgood)
        .bind(payload.failure_message())
        .execute(&mut **tx)
        .await?;
    require_state_row_updated(update_result.rows_affected())
}

fn require_state_row_updated(rows_affected: u64) -> Result<(), ApiError> {
    if rows_affected == 0 {
        return Err(ApiError::Safe(
            "Student grade state was not found for the result".into(),
        ));
    }
    Ok(())
}

pub async fn mark_complete(
    neon_db: &PgPool,
    job_id: Uuid,
    worker_id: &str,
    lease_token: Uuid,
    payload: WorkerCompletionRequest,
) -> Result<(), ApiError> {
    let completion = serde_json::to_value(payload)
        .map_err(|_| ApiError::Safe("Unable to encode worker completion".into()))?;
    let update_result = query(QUERY_COMPLETE)
        .bind(job_id)
        .bind(completion)
        .bind(worker_id)
        .bind(lease_token)
        .execute(neon_db)
        .await?;
    require_job_mutation(update_result.rows_affected())
}

pub async fn mark_failed(
    neon_db: &PgPool,
    job_id: Uuid,
    worker_id: &str,
    lease_token: Uuid,
    payload: WorkerFailRequest,
) -> Result<(), ApiError> {
    let update_result = query(QUERY_FAIL)
        .bind(job_id)
        .bind(payload.storage_message())
        .bind(worker_id)
        .bind(lease_token)
        .execute(neon_db)
        .await?;
    require_job_mutation(update_result.rows_affected())
}

fn require_job_mutation(rows_affected: u64) -> Result<(), ApiError> {
    if rows_affected == 0 {
        return Err(ApiError::NotFound);
    }
    Ok(())
}

fn duplicate_result_identity_decision(
    existing_student_id: i64,
    existing_payload: &serde_json::Value,
    requested_student_id: i64,
    incoming_payload: &serde_json::Value,
) -> Result<(), ApiError> {
    if existing_student_id == requested_student_id && existing_payload == incoming_payload {
        Ok(())
    } else {
        Err(ApiError::Conflict(
            "Idempotency key was already used for a different result".into(),
        ))
    }
}

#[cfg(test)]
fn attempt_is_exhausted(attempt_count: i32) -> bool {
    attempt_count >= 3
}

pub async fn latest_results(
    neon_db: &PgPool,
    franchise_id: Option<i32>,
    limit: i64,
) -> Result<Vec<JobResult>, ApiError> {
    let bounded = limit.clamp(1, 200);
    Ok(query_as::<_, JobResult>(QUERY_LATEST_RESULTS)
        .bind(franchise_id)
        .bind(bounded)
        .fetch_all(neon_db)
        .await?)
}

#[cfg(test)]
mod tests {
    use super::{
        attempt_is_exhausted, duplicate_result_identity_decision, require_job_mutation,
        require_state_row_updated, QUERY_CLAIM_DASHBOARD_NONCE, QUERY_CLAIM_NEXT_JOB,
        QUERY_CLEANUP_DASHBOARD_NONCES, QUERY_COMPLETE, QUERY_CREATE_JOB,
        QUERY_CREATE_SCHEDULER_JOB, QUERY_FAIL, QUERY_FIND_SCHEDULER_JOB, QUERY_HEARTBEAT,
        QUERY_INSERT_EVENT, QUERY_LOCK_ACTIVE_LEASE, QUERY_RECONCILE_CREATE_STATE,
        QUERY_RECONCILE_DELETE_STATE,
    };

    fn normalized_sql(value: &str) -> String {
        value.split_whitespace().collect::<Vec<_>>().join(" ")
    }

    #[test]
    fn applying_result_state_requires_an_existing_state_row() {
        assert!(require_state_row_updated(1).is_ok());
        assert!(require_state_row_updated(0).is_err());
    }

    #[test]
    fn job_artifact_mutations_require_an_existing_job_row() {
        assert!(require_job_mutation(1).is_ok());
        assert!(matches!(
            require_job_mutation(0),
            Err(crate::error::ApiError::NotFound)
        ));
    }

    #[test]
    fn worker_claim_and_lifecycle_queries_are_atomic_and_owner_scoped() {
        assert!(QUERY_CLAIM_NEXT_JOB.contains("FOR UPDATE SKIP LOCKED"));
        assert!(QUERY_CLAIM_NEXT_JOB.contains("UPDATE grade_scrape_jobs AS jobs"));
        assert!(QUERY_CLAIM_NEXT_JOB.contains("lease_token"));
        assert!(QUERY_CLAIM_NEXT_JOB.contains("lease_expires_at"));
        assert!(QUERY_CLAIM_NEXT_JOB.contains("attempt_count"));
        assert!(QUERY_CLAIM_NEXT_JOB.contains("expired_exhausted AS"));
        assert!(QUERY_CLAIM_NEXT_JOB.contains("cleanup_candidate AS"));
        assert!(QUERY_CLAIM_NEXT_JOB.contains("lease_expires_at IS NULL"));
        assert!(QUERY_CLAIM_NEXT_JOB.contains("heartbeat = NULL"));
        assert!(QUERY_CLAIM_NEXT_JOB.contains("COALESCE(jobs.attempt_count, 0) < 3"));
        assert!(QUERY_CLAIM_NEXT_JOB
            .contains("jobs.lease_expires_at IS NULL OR jobs.lease_expires_at <= NOW()"));
        assert!(!QUERY_CLAIM_NEXT_JOB.contains("COUNT(*) FROM expired_exhausted"));
        assert!(QUERY_CLAIM_NEXT_JOB.contains("cleanup_candidate"));
        assert!(QUERY_LOCK_ACTIVE_LEASE.contains("FOR UPDATE"));
        assert!(QUERY_LOCK_ACTIVE_LEASE.contains("lease_token"));
        assert!(QUERY_LOCK_ACTIVE_LEASE.contains("lease_expires_at > NOW()"));
        for query in [
            QUERY_HEARTBEAT,
            QUERY_INSERT_EVENT,
            QUERY_COMPLETE,
            QUERY_FAIL,
        ] {
            assert!(query.contains("worker_id"));
            assert!(query.contains("status = 'running'"));
            assert!(query.contains("lease_token"));
            assert!(query.contains("lease_expires_at > NOW()"));
        }
        assert!(QUERY_INSERT_EVENT.contains("WITH active_worker_lease AS"));
        assert!(QUERY_INSERT_EVENT.contains("FOR UPDATE"));
        assert!(QUERY_CLAIM_NEXT_JOB.contains("target_worker_id = $1"));
        assert!(QUERY_CREATE_SCHEDULER_JOB.contains("target_worker_id"));
    }

    #[test]
    fn dashboard_nonce_claim_is_atomic_and_cleanup_is_bounded() {
        assert!(
            QUERY_CLAIM_DASHBOARD_NONCE.contains("ON CONFLICT (identity_hash, nonce) DO UPDATE")
        );
        assert!(QUERY_CLAIM_DASHBOARD_NONCE.contains("dashboard_replay_nonces.expires_at < now()"));
        assert!(QUERY_CLAIM_DASHBOARD_NONCE.contains("RETURNING 1"));
        assert!(QUERY_CLEANUP_DASHBOARD_NONCES.contains("LIMIT $1"));
        assert!(QUERY_CLEANUP_DASHBOARD_NONCES.contains("WHERE expires_at < now()"));
    }

    #[test]
    fn scheduler_idempotency_and_reconciliation_queries_are_atomic() {
        assert!(QUERY_CREATE_SCHEDULER_JOB.contains("scheduler_identity"));
        assert!(QUERY_CREATE_SCHEDULER_JOB.contains("scheduler_idempotency_key"));
        assert!(QUERY_CREATE_SCHEDULER_JOB.contains("scheduler_request_hash"));
        assert!(QUERY_CREATE_SCHEDULER_JOB.contains("ON CONFLICT"));
        assert!(QUERY_FIND_SCHEDULER_JOB.contains("scheduler_request_hash"));
        assert!(QUERY_RECONCILE_CREATE_STATE.contains("ON CONFLICT (crmstudentid) DO NOTHING"));
        assert!(QUERY_RECONCILE_DELETE_STATE.contains("NOT (crmstudentid::bigint = ANY"));
    }

    #[test]
    fn scheduler_lookup_stays_identity_and_idempotency_only() {
        let sql = normalized_sql(QUERY_FIND_SCHEDULER_JOB);
        assert!(sql.contains("WHERE scheduler_identity = $1 AND scheduler_idempotency_key = $2"));
        assert!(!sql.contains("target_worker_id"));
    }

    #[test]
    fn scheduler_insert_persists_target_worker() {
        let sql = normalized_sql(QUERY_CREATE_SCHEDULER_JOB);
        assert!(sql.contains("kind, franchise_id, student_id, target_worker_id, status"));
        assert!(sql.contains("VALUES ($1, $2, $3, $4, 'queued'"));
    }

    #[test]
    fn manual_insert_persists_default_target() {
        let sql = normalized_sql(QUERY_CREATE_JOB);
        assert!(sql.contains("kind, franchise_id, student_id, target_worker_id, status"));
        assert!(sql.contains("VALUES ($1, $2, $3, $4, 'queued'"));
    }

    #[test]
    fn claim_filters_authenticated_target_before_locking() {
        let sql = normalized_sql(QUERY_CLAIM_NEXT_JOB);
        assert!(sql.contains(
            "candidate AS ( SELECT jobs.id FROM grade_scrape_jobs AS jobs WHERE jobs.target_worker_id = $1 AND ("
        ));
        let target = sql.rfind("jobs.target_worker_id = $1").unwrap();
        let lock = sql.rfind("FOR UPDATE SKIP LOCKED").unwrap();
        assert!(target < lock);
    }

    #[test]
    fn claim_cleanup_cannot_mutate_another_target() {
        let sql = normalized_sql(QUERY_CLAIM_NEXT_JOB);
        assert!(sql.contains(
            "cleanup_candidate AS ( SELECT id FROM grade_scrape_jobs WHERE target_worker_id = $1 AND status = 'running'"
        ));
        assert_eq!(sql.matches("target_worker_id = $1").count(), 2);
    }

    #[test]
    fn duplicate_result_payloads_are_idempotent_only_when_canonical_payloads_match() {
        let payload = serde_json::json!({"status": "synced", "parsed_grades": {"math": 95}});
        assert!(duplicate_result_identity_decision(42, &payload, 42, &payload).is_ok());
        assert!(duplicate_result_identity_decision(
            42,
            &payload,
            42,
            &serde_json::json!({"status": "failed"}),
        )
        .is_err());
    }

    #[test]
    fn result_uuid_is_bound_to_student_and_payload() {
        let payload = serde_json::json!({"status": "synced"});
        assert!(matches!(
            duplicate_result_identity_decision(41, &payload, 42, &payload),
            Err(crate::error::ApiError::Conflict(_))
        ));
    }

    #[test]
    fn attempt_cap_marks_the_third_expired_lease_as_exhausted() {
        assert!(!attempt_is_exhausted(2));
        assert!(attempt_is_exhausted(3));
        assert!(attempt_is_exhausted(4));
    }

    #[test]
    fn lease_migration_backfills_legacy_running_jobs_before_reclaim() {
        let migration = include_str!("../migrations/003_worker_job_leases.sql");
        assert!(migration.contains("lease_expires_at = NOW()"));
        assert!(migration.contains("status = 'running'"));
    }
}
