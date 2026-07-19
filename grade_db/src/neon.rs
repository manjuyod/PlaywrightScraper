use std::collections::HashMap;

use async_trait::async_trait;
use chrono::{DateTime, Utc};
use serde_json::Value;
use sqlx::postgres::PgPoolOptions;
use sqlx::{PgPool, Postgres, Row, Transaction};
use uuid::Uuid;

use crate::error::AppError;
use crate::models::{
    ActiveJob, JobKind, JobLease, JobStartRequest, ResultOutcome, StudentGradeState,
};
use crate::service::{NeonGateway, NeonResultWrite};

pub mod sql {
    pub const PING: &str = "SELECT 1";
    pub const SCHEMA_READY: &str = r#"
SELECT NOT EXISTS (
    SELECT 1
    FROM (
        VALUES
            ('students_grades_20262027', 'crmstudentid'),
            ('students_grades_20262027', 'portal2'),
            ('students_grades_20262027', 'p2username'),
            ('students_grades_20262027', 'p2password'),
            ('students_grades_20262027', 'weeklydata'),
            ('students_grades_20262027', 'weekly_agenda'),
            ('students_grades_20262027', 'portal'),
            ('students_grades_20262027', 'track_agenda'),
            ('students_grades_20262027', 'auth_type'),
            ('students_grades_20262027', 'auth_answers'),
            ('students_grades_20262027', 'status'),
            ('students_grades_20262027', 'passwordgood'),
            ('students_grades_20262027', 'error_msg'),
            ('grade_scrape_jobs', 'id'),
            ('grade_scrape_jobs', 'kind'),
            ('grade_scrape_jobs', 'status'),
            ('grade_scrape_jobs', 'franchise_id'),
            ('grade_scrape_jobs', 'student_id'),
            ('grade_scrape_jobs', 'runner_id'),
            ('grade_scrape_jobs', 'lease_token'),
            ('grade_scrape_jobs', 'lease_expires_at'),
            ('grade_scrape_jobs', 'progress'),
            ('grade_scrape_jobs', 'summary'),
            ('grade_scrape_jobs', 'error_msg'),
            ('grade_scrape_job_events', 'job_id'),
            ('grade_scrape_job_events', 'level'),
            ('grade_scrape_job_events', 'code'),
            ('grade_scrape_job_events', 'message'),
            ('grade_scrape_job_events', 'crmstudentid'),
            ('grade_scrape_job_events', 'payload'),
            ('grade_scrape_results', 'job_id'),
            ('grade_scrape_results', 'crmstudentid'),
            ('grade_scrape_results', 'idempotency_key'),
            ('grade_scrape_results', 'payload'),
            ('grade_scrape_results', 'applied'),
            ('grade_scrape_results', 'rejection_code')
    ) AS required(table_name, column_name)
    WHERE NOT EXISTS (
        SELECT 1
        FROM information_schema.columns AS actual
        WHERE actual.table_schema = 'public'
          AND actual.table_name = required.table_name
          AND actual.column_name = required.column_name
    )
)
"#;
    pub const ENSURE_STATES: &str = r#"
INSERT INTO students_grades_20262027 (crmstudentid)
SELECT value FROM unnest($1::bigint[]) AS value
ON CONFLICT (crmstudentid) DO NOTHING
"#;
    pub const STATES_BY_IDS: &str = r#"
SELECT crmstudentid, portal2, p2username, p2password, portal,
       COALESCE(track_agenda, false) AS track_agenda,
       auth_type, COALESCE(auth_answers, '[]'::jsonb) AS auth_answers,
       status, passwordgood
FROM students_grades_20262027
WHERE crmstudentid = ANY($1::bigint[])
"#;
    pub const LOCK_JOB_KIND: &str = "SELECT pg_advisory_xact_lock(hashtext('grade-db:' || $1))";
    pub const EXPIRE_JOBS: &str = r#"
WITH expired AS (
    UPDATE grade_scrape_jobs
    SET status = 'failed', error_msg = 'lease_expired', completed_at = now(), updated_at = now()
    WHERE kind = $1 AND status = 'running' AND lease_expires_at <= now()
    RETURNING id
)
INSERT INTO grade_scrape_job_events (job_id, level, code, message, payload)
SELECT id, 'error', 'lease_expired', 'lease_expired', '{}'::jsonb
FROM expired
"#;
    pub const FIND_CONFLICT: &str = r#"
SELECT id
FROM grade_scrape_jobs
WHERE kind = $1
  AND status = 'running'
  AND lease_expires_at > now()
  AND (franchise_id IS NULL OR $2::integer IS NULL OR franchise_id = $2)
LIMIT 1
"#;
    pub const INSERT_JOB: &str = r#"
INSERT INTO grade_scrape_jobs (
    kind, status, franchise_id, student_id, runner_id,
    lease_token, lease_expires_at, progress, started_at, updated_at
)
VALUES ($1, 'running', $2, $3, $4, $5,
        now() + ($6::bigint * interval '1 second'), $7, now(), now())
RETURNING id, lease_expires_at
"#;
    pub const INSERT_EVENT: &str = r#"
INSERT INTO grade_scrape_job_events (job_id, level, code, message, crmstudentid, payload)
VALUES ($1, $2, $3, $3, $4, $5)
"#;
    pub const ACTIVE_JOB: &str = r#"
SELECT id, lease_token, kind, franchise_id, student_id
FROM grade_scrape_jobs
WHERE id = $1 AND lease_token = $2 AND runner_id = $3
  AND status = 'running' AND lease_expires_at > now()
"#;
    pub const LOCK_ACTIVE_JOB: &str = r#"
SELECT id
FROM grade_scrape_jobs
WHERE id = $1 AND lease_token = $2 AND runner_id = $3
  AND status = 'running' AND lease_expires_at > now()
FOR UPDATE
"#;
    pub const HEARTBEAT: &str = r#"
UPDATE grade_scrape_jobs
SET progress = $4, lease_expires_at = now() + ($5::bigint * interval '1 second'), updated_at = now()
WHERE id = $1 AND lease_token = $2 AND runner_id = $3
  AND status = 'running' AND lease_expires_at > now()
"#;
    pub const COMPLETE_JOB: &str = r#"
UPDATE grade_scrape_jobs
SET status = 'complete', progress = $4, summary = $4,
    completed_at = now(), updated_at = now()
WHERE id = $1 AND lease_token = $2 AND runner_id = $3
  AND status = 'running' AND lease_expires_at > now()
"#;
    pub const FAIL_JOB: &str = r#"
UPDATE grade_scrape_jobs
SET status = 'failed', error_msg = $4, completed_at = now(), updated_at = now()
WHERE id = $1 AND lease_token = $2 AND runner_id = $3
  AND status = 'running' AND lease_expires_at > now()
"#;
    pub const INSERT_RESULT: &str = r#"
INSERT INTO grade_scrape_results (
    job_id, crmstudentid, idempotency_key, payload, applied, rejection_code
)
VALUES ($1, $2, $3, $4, $5, $6)
ON CONFLICT (job_id, idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING
RETURNING id
"#;
    pub const EXISTING_RESULT: &str = r#"
SELECT crmstudentid, payload, applied, rejection_code
FROM grade_scrape_results
WHERE job_id = $1 AND idempotency_key = $2
"#;
    pub const APPLY_GRADE: &str = r#"
UPDATE students_grades_20262027
SET weeklydata = COALESCE(weeklydata, '{}'::jsonb)
        || jsonb_build_object(
            to_char(date_trunc('week', now())::date, 'YYYY-MM-DD'),
            $2::jsonb
        ),
    passwordgood = true, status = 'synced', error_msg = NULL, updated_at = now()
WHERE crmstudentid = $1
"#;
    pub const APPLY_AGENDA: &str = r#"
UPDATE students_grades_20262027
SET weekly_agenda = $2::jsonb,
    status = 'synced', error_msg = NULL, updated_at = now()
WHERE crmstudentid = $1
"#;
    pub const APPLY_FAILURE: &str = r#"
UPDATE students_grades_20262027
SET status = 'error', passwordgood = COALESCE($2, passwordgood),
    error_msg = $3, updated_at = now()
WHERE crmstudentid = $1
"#;
}

#[derive(Clone)]
pub struct PostgresNeonGateway {
    pool: PgPool,
    runner_id: String,
    lease_seconds: i64,
}

impl PostgresNeonGateway {
    pub async fn connect(
        database_url: &str,
        runner_id: String,
        lease_seconds: i64,
    ) -> Result<Self, AppError> {
        let pool = PgPoolOptions::new()
            .max_connections(4)
            .connect(database_url)
            .await
            .map_err(neon_error)?;
        Ok(Self {
            pool,
            runner_id,
            lease_seconds,
        })
    }

    pub async fn schema_ready(&self) -> Result<bool, AppError> {
        sqlx::query_scalar(sql::SCHEMA_READY)
            .fetch_one(&self.pool)
            .await
            .map_err(neon_error)
    }

    pub async fn heartbeat(
        &self,
        job_id: Uuid,
        lease_token: Uuid,
        progress: &Value,
    ) -> Result<(), AppError> {
        let updated = sqlx::query(sql::HEARTBEAT)
            .bind(job_id)
            .bind(lease_token)
            .bind(&self.runner_id)
            .bind(progress)
            .bind(self.lease_seconds)
            .execute(&self.pool)
            .await
            .map_err(neon_error)?
            .rows_affected();
        require_lease(updated)
    }

    pub async fn complete(
        &self,
        job_id: Uuid,
        lease_token: Uuid,
        progress: &Value,
    ) -> Result<(), AppError> {
        let mut tx = self.pool.begin().await.map_err(neon_error)?;
        let updated = sqlx::query(sql::COMPLETE_JOB)
            .bind(job_id)
            .bind(lease_token)
            .bind(&self.runner_id)
            .bind(progress)
            .execute(&mut *tx)
            .await
            .map_err(neon_error)?
            .rows_affected();
        require_lease(updated)?;
        insert_event(&mut tx, job_id, "info", "job_complete", None).await?;
        tx.commit().await.map_err(neon_error)
    }

    pub async fn fail(&self, job_id: Uuid, lease_token: Uuid, code: &str) -> Result<(), AppError> {
        let mut tx = self.pool.begin().await.map_err(neon_error)?;
        let updated = sqlx::query(sql::FAIL_JOB)
            .bind(job_id)
            .bind(lease_token)
            .bind(&self.runner_id)
            .bind(code)
            .execute(&mut *tx)
            .await
            .map_err(neon_error)?
            .rows_affected();
        require_lease(updated)?;
        insert_event(&mut tx, job_id, "error", code, None).await?;
        tx.commit().await.map_err(neon_error)
    }
}

#[async_trait]
impl NeonGateway for PostgresNeonGateway {
    async fn ping(&self) -> Result<(), AppError> {
        sqlx::query(sql::PING)
            .execute(&self.pool)
            .await
            .map_err(neon_error)?;
        Ok(())
    }

    async fn ensure_states(&self, crm_ids: &[i64]) -> Result<(), AppError> {
        if crm_ids.is_empty() {
            return Ok(());
        }
        sqlx::query(sql::ENSURE_STATES)
            .bind(crm_ids)
            .execute(&self.pool)
            .await
            .map_err(neon_error)?;
        Ok(())
    }

    async fn states_by_crm_ids(
        &self,
        crm_ids: &[i64],
    ) -> Result<HashMap<i64, StudentGradeState>, AppError> {
        if crm_ids.is_empty() {
            return Ok(HashMap::new());
        }
        let rows = sqlx::query(sql::STATES_BY_IDS)
            .bind(crm_ids)
            .fetch_all(&self.pool)
            .await
            .map_err(neon_error)?;
        rows.into_iter()
            .map(|row| {
                let crmstudentid = row.try_get("crmstudentid").map_err(neon_error)?;
                Ok((
                    crmstudentid,
                    StudentGradeState {
                        crmstudentid,
                        portal2: row.try_get("portal2").map_err(neon_error)?,
                        p2username: row.try_get("p2username").map_err(neon_error)?,
                        p2password: row.try_get("p2password").map_err(neon_error)?,
                        portal: row.try_get("portal").map_err(neon_error)?,
                        track_agenda: row.try_get("track_agenda").map_err(neon_error)?,
                        auth_type: row.try_get("auth_type").map_err(neon_error)?,
                        auth_answers: row.try_get("auth_answers").map_err(neon_error)?,
                        status: row.try_get("status").map_err(neon_error)?,
                        passwordgood: row.try_get("passwordgood").map_err(neon_error)?,
                    },
                ))
            })
            .collect()
    }

    async fn start_job(
        &self,
        request: &JobStartRequest,
        franchise_id: Option<i32>,
        runner_id: &str,
        lease_seconds: i64,
        total: u32,
    ) -> Result<JobLease, AppError> {
        let mut tx = self.pool.begin().await.map_err(neon_error)?;
        sqlx::query(sql::LOCK_JOB_KIND)
            .bind(request.kind.as_str())
            .execute(&mut *tx)
            .await
            .map_err(neon_error)?;
        sqlx::query(sql::EXPIRE_JOBS)
            .bind(request.kind.as_str())
            .execute(&mut *tx)
            .await
            .map_err(neon_error)?;
        let conflict: Option<Uuid> = sqlx::query_scalar(sql::FIND_CONFLICT)
            .bind(request.kind.as_str())
            .bind(franchise_id)
            .fetch_optional(&mut *tx)
            .await
            .map_err(neon_error)?;
        if conflict.is_some() {
            return Err(AppError::Conflict);
        }

        let lease_token = Uuid::new_v4();
        let progress = serde_json::json!({
            "total": total,
            "attempted": 0,
            "success": 0,
            "errors": 0,
        });
        let row = sqlx::query(sql::INSERT_JOB)
            .bind(request.kind.as_str())
            .bind(franchise_id)
            .bind(request.student_id)
            .bind(runner_id)
            .bind(lease_token)
            .bind(lease_seconds)
            .bind(progress)
            .fetch_one(&mut *tx)
            .await
            .map_err(neon_error)?;
        let job_id: Uuid = row.try_get("id").map_err(neon_error)?;
        let lease_expires_at: DateTime<Utc> =
            row.try_get("lease_expires_at").map_err(neon_error)?;
        insert_event(&mut tx, job_id, "info", "job_started", None).await?;
        tx.commit().await.map_err(neon_error)?;
        Ok(JobLease {
            job_id,
            lease_token,
            lease_expires_at,
            kind: request.kind,
            franchise_id,
            student_id: request.student_id,
        })
    }

    async fn active_job(&self, job_id: Uuid, lease_token: Uuid) -> Result<ActiveJob, AppError> {
        let row = sqlx::query(sql::ACTIVE_JOB)
            .bind(job_id)
            .bind(lease_token)
            .bind(&self.runner_id)
            .fetch_optional(&self.pool)
            .await
            .map_err(neon_error)?
            .ok_or(AppError::LeaseExpired)?;
        Ok(ActiveJob {
            job_id: row.try_get("id").map_err(neon_error)?,
            lease_token: row.try_get("lease_token").map_err(neon_error)?,
            kind: parse_job_kind(row.try_get("kind").map_err(neon_error)?)?,
            franchise_id: row.try_get("franchise_id").map_err(neon_error)?,
            student_id: row.try_get("student_id").map_err(neon_error)?,
        })
    }

    async fn record_result(&self, write: NeonResultWrite) -> Result<bool, AppError> {
        let mut tx = self.pool.begin().await.map_err(neon_error)?;
        let active: Option<Uuid> = sqlx::query_scalar(sql::LOCK_ACTIVE_JOB)
            .bind(write.request.job_id)
            .bind(write.request.lease_token)
            .bind(&self.runner_id)
            .fetch_optional(&mut *tx)
            .await
            .map_err(neon_error)?;
        if active.is_none() {
            return Err(AppError::LeaseExpired);
        }

        let inserted: Option<i64> = sqlx::query_scalar(sql::INSERT_RESULT)
            .bind(write.request.job_id)
            .bind(write.request.crmstudentid)
            .bind(write.idempotency_key)
            .bind(&write.audit_payload)
            .bind(write.applied)
            .bind(&write.rejection_code)
            .fetch_optional(&mut *tx)
            .await
            .map_err(neon_error)?;
        if inserted.is_none() {
            let existing = sqlx::query(sql::EXISTING_RESULT)
                .bind(write.request.job_id)
                .bind(write.idempotency_key)
                .fetch_optional(&mut *tx)
                .await
                .map_err(neon_error)?
                .ok_or(AppError::Internal)?;
            let same = existing
                .try_get::<i64, _>("crmstudentid")
                .map_err(neon_error)?
                == write.request.crmstudentid
                && existing
                    .try_get::<Value, _>("payload")
                    .map_err(neon_error)?
                    == write.audit_payload
                && existing.try_get::<bool, _>("applied").map_err(neon_error)? == write.applied
                && existing
                    .try_get::<Option<String>, _>("rejection_code")
                    .map_err(neon_error)?
                    == write.rejection_code;
            if !same {
                return Err(AppError::Conflict);
            }
            tx.commit().await.map_err(neon_error)?;
            return Ok(true);
        }

        if write.applied {
            ensure_state_tx(&mut tx, write.request.crmstudentid).await?;
            apply_outcome(&mut tx, write.request.crmstudentid, &write.request.outcome).await?;
        } else {
            insert_event(
                &mut tx,
                write.request.job_id,
                "warn",
                write.rejection_code.as_deref().unwrap_or("result_rejected"),
                Some(write.request.crmstudentid),
            )
            .await?;
        }
        tx.commit().await.map_err(neon_error)?;
        Ok(false)
    }
}

async fn ensure_state_tx(
    tx: &mut Transaction<'_, Postgres>,
    crmstudentid: i64,
) -> Result<(), AppError> {
    sqlx::query(
        "INSERT INTO students_grades_20262027 (crmstudentid) VALUES ($1) ON CONFLICT (crmstudentid) DO NOTHING",
    )
    .bind(crmstudentid)
    .execute(&mut **tx)
    .await
    .map_err(neon_error)?;
    Ok(())
}

async fn apply_outcome(
    tx: &mut Transaction<'_, Postgres>,
    crmstudentid: i64,
    outcome: &ResultOutcome,
) -> Result<(), AppError> {
    let result = match outcome {
        ResultOutcome::GradeSuccess { parsed_grades } => {
            sqlx::query(sql::APPLY_GRADE)
                .bind(crmstudentid)
                .bind(parsed_grades)
                .execute(&mut **tx)
                .await
        }
        ResultOutcome::AgendaSuccess { weekly_agenda } => {
            sqlx::query(sql::APPLY_AGENDA)
                .bind(crmstudentid)
                .bind(weekly_agenda)
                .execute(&mut **tx)
                .await
        }
        ResultOutcome::Failure { code, passwordgood } => {
            sqlx::query(sql::APPLY_FAILURE)
                .bind(crmstudentid)
                .bind(passwordgood)
                .bind(code)
                .execute(&mut **tx)
                .await
        }
    }
    .map_err(neon_error)?;
    if result.rows_affected() == 1 {
        Ok(())
    } else {
        Err(AppError::Internal)
    }
}

async fn insert_event(
    tx: &mut Transaction<'_, Postgres>,
    job_id: Uuid,
    level: &str,
    code: &str,
    crmstudentid: Option<i64>,
) -> Result<(), AppError> {
    sqlx::query(sql::INSERT_EVENT)
        .bind(job_id)
        .bind(level)
        .bind(code)
        .bind(crmstudentid)
        .bind(serde_json::json!({}))
        .execute(&mut **tx)
        .await
        .map_err(neon_error)?;
    Ok(())
}

fn require_lease(rows_affected: u64) -> Result<(), AppError> {
    if rows_affected == 1 {
        Ok(())
    } else {
        Err(AppError::LeaseExpired)
    }
}

fn parse_job_kind(value: String) -> Result<JobKind, AppError> {
    match value.as_str() {
        "grade" => Ok(JobKind::Grade),
        "agenda" => Ok(JobKind::Agenda),
        _ => Err(AppError::Internal),
    }
}

fn neon_error<T>(_error: T) -> AppError {
    AppError::Dependency("neon_unavailable")
}
