use axum::{
    extract::{Extension, Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    routing::{get, post, put},
    Json, Router,
};
use serde::Deserialize;
use serde_json::json;
use std::collections::BTreeMap;
use std::time::Duration;
use uuid::Uuid;

use crate::api_keys::identify_basic_key;
use crate::auth::{
    dashboard_auth_middleware, operator_auth_middleware, scheduler_auth_middleware,
    worker_auth_middleware, DashboardAuthClaims, OperatorAuthClaims, SchedulerAuthClaims,
    WorkerAuthClaims,
};
use crate::error::ApiError;
use crate::models::{
    worker_owns_running_job, worker_result_state_action, AdminHealthErrorCategory,
    AuthLoginRequest, AuthLoginResponse, DashboardHealthResponse, DashboardResponse,
    FranchiseHealthCounts, JobEventsResponse, LatestResultsResponse, ManualPullRequest,
    ManualPullResponse, OperatorAlternateCredentialsRequest, OperatorCancelJobRequest,
    OperatorRetargetJobRequest, PublicJob, PublicJobEvent, PublicJobResult, PublicStudent,
    ReconciliationSummary, SchedulerJobRequest, StudentQuery, StudentsResponse,
    WorkerCompletionRequest, WorkerEventRequest, WorkerFailRequest, WorkerHeartbeatRequest,
    WorkerJobContext, WorkerJobsResponse, WorkerResultRequest, WorkerResultStateAction,
};
use crate::queries;
use crate::rate_limit::{ApiRole, READINESS_REQUESTS_PER_MINUTE};
use crate::state::AppState;

pub fn create_router(state: AppState) -> Router {
    let public = Router::new()
        .route("/livez", get(livez))
        .route("/readyz", get(readyz));

    let dashboard = Router::new()
        .route("/api/auth/login", post(auth_login))
        .route("/api/dashboard", get(dashboard))
        .route("/api/dashboard/health", get(dashboard_health))
        .route("/api/students", get(list_students))
        .route("/api/students/:id", get(get_student))
        .route("/api/jobs/current", get(current_jobs))
        .route("/api/jobs/manual-pull", post(manual_pull))
        .route("/api/jobs/:job_id", get(job_by_id))
        .route("/api/jobs/:job_id/events", get(job_events))
        .route("/api/results/latest", get(latest_results))
        .route_layer(axum::middleware::from_fn_with_state(
            state.clone(),
            dashboard_auth_middleware,
        ));

    let worker = Router::new()
        .route("/api/worker/jobs/claim", post(claim_job))
        .route("/api/worker/jobs/:job_id/context", get(job_context))
        .route("/api/worker/jobs/:job_id/heartbeat", post(heartbeat_job))
        .route("/api/worker/jobs/:job_id/events", post(event_job))
        .route("/api/worker/jobs/:job_id/results", post(results_job))
        .route("/api/worker/jobs/:job_id/complete", post(complete_job))
        .route("/api/worker/jobs/:job_id/fail", post(fail_job))
        .route_layer(axum::middleware::from_fn_with_state(
            state.clone(),
            worker_auth_middleware,
        ));

    let scheduler = Router::new()
        .route("/api/scheduler/jobs", post(scheduler_job))
        .route(
            "/api/scheduler/reconcile-students",
            post(scheduler_reconcile_students),
        )
        .route_layer(axum::middleware::from_fn_with_state(
            state.clone(),
            scheduler_auth_middleware,
        ));

    let operator = Router::new()
        .route(
            "/api/operator/jobs/:job_id/retarget",
            post(operator_retarget_job),
        )
        .route(
            "/api/operator/jobs/:job_id/cancel",
            post(operator_cancel_job),
        )
        .route(
            "/api/operator/students/:student_id/alternate-credentials",
            put(operator_put_alternate_credentials).delete(operator_delete_alternate_credentials),
        )
        .route_layer(axum::middleware::from_fn_with_state(
            state.clone(),
            operator_auth_middleware,
        ));

    Router::new()
        .merge(public)
        .merge(dashboard)
        .merge(worker)
        .merge(scheduler)
        .merge(operator)
        .with_state(state)
}

fn scoped_franchise(claims: &DashboardAuthClaims) -> Result<i32, ApiError> {
    claims
        .franchise_id
        .as_deref()
        .and_then(|value| value.parse::<i32>().ok())
        .filter(|value| *value > 0)
        .ok_or(ApiError::Unauthorized)
}

fn scoped_role(claims: &DashboardAuthClaims) -> Option<i32> {
    claims
        .role
        .as_deref()
        .and_then(|value| value.parse::<i32>().ok())
}

fn authorize_scheduler_job(
    claims: &SchedulerAuthClaims,
    request: &SchedulerJobRequest,
) -> Result<(), ApiError> {
    request.validate()?;
    if !claims.franchise_ids.contains(&request.franchise_id)
        || !claims.target_worker_ids.contains(&request.target_worker_id)
    {
        return Err(ApiError::Forbidden);
    }
    Ok(())
}

fn authorize_scheduler_reconcile(claims: &SchedulerAuthClaims) -> Result<(), ApiError> {
    if !claims.can_reconcile {
        return Err(ApiError::Forbidden);
    }
    Ok(())
}

fn manual_pull_target(state: &AppState) -> &str {
    &state.config.default_worker_id
}

fn authorize_operator_retarget(
    state: &AppState,
    request: &OperatorRetargetJobRequest,
) -> Result<(), ApiError> {
    request.validate()?;
    if !state
        .config
        .worker_api_keyring
        .contains_key(&request.target_worker_id)
    {
        return Err(ApiError::BadRequest(
            "Target worker is not configured".into(),
        ));
    }
    Ok(())
}

fn active_worker_lease(claims: &WorkerAuthClaims) -> Result<Uuid, ApiError> {
    claims.lease_token.ok_or(ApiError::Unauthorized)
}

async fn merged_students(
    state: &AppState,
    franchise_id: i32,
    student_id: Option<i64>,
) -> Result<Vec<PublicStudent>, ApiError> {
    let crm_students = state
        .crm
        .list_students(Some(franchise_id), student_id)
        .await?;
    let eligible_ids: Vec<i64> = crm_students
        .iter()
        .filter(|student| student.is_grade_portal_eligible())
        .map(|student| student.crmstudentid)
        .collect();
    let state_by_id = if eligible_ids.is_empty() {
        Default::default()
    } else {
        queries::ensure_grades_table(&state.neon_db).await?;
        queries::states_by_crm_ids(&state.neon_db, &eligible_ids).await?
    };
    Ok(crm_students
        .iter()
        .map(|student| {
            let student_state = student
                .is_grade_portal_eligible()
                .then(|| state_by_id.get(&student.crmstudentid))
                .flatten();
            crate::models::merge_public_student(student, student_state)
        })
        .collect())
}

fn worker_alternate_credentials(
    state: Option<&crate::models::StudentGradeState>,
    config: &crate::config::ApiConfig,
    crmstudentid: i64,
) -> Result<Option<crate::credentials::AlternateCredentials>, ApiError> {
    let Some(state) = state else {
        return Ok(None);
    };
    if let Some(envelope) = state.encrypted_alternate_credentials()? {
        return config
            .alternate_credential_keyring
            .decrypt(crmstudentid, &envelope)
            .map(Some)
            .map_err(|_| ApiError::Unavailable);
    }
    if !config.allow_plaintext_alternate_credentials {
        return Ok(None);
    }
    match (state.p2username.as_ref(), state.p2password.as_ref()) {
        (None, None) => Ok(None),
        (Some(username), Some(password))
            if !username.trim().is_empty() && !password.trim().is_empty() =>
        {
            Ok(Some(crate::credentials::AlternateCredentials {
                username: username.clone(),
                password: password.clone(),
            }))
        }
        _ => Err(ApiError::Unavailable),
    }
}

pub async fn livez() -> Json<serde_json::Value> {
    Json(json!({"status": "ok"}))
}

pub async fn readyz(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> Result<(StatusCode, Json<serde_json::Value>), ApiError> {
    let provided = headers
        .get(axum::http::header::AUTHORIZATION)
        .and_then(|value| value.to_str().ok())
        .unwrap_or("");
    let token = provided
        .strip_prefix("Bearer ")
        .or_else(|| provided.strip_prefix("bearer "))
        .unwrap_or("");

    let authenticated = identify_basic_key(
        &state.config.readiness_api_keyring,
        token,
        chrono::Utc::now(),
    )
    .ok_or(ApiError::Unauthorized)?;
    state.rate_limiter.check(
        ApiRole::Readiness,
        &authenticated.identity,
        READINESS_REQUESTS_PER_MINUTE,
    )?;

    let timeout = Duration::from_millis(state.config.readiness_timeout_millis);
    let neon_probe = tokio::time::timeout(timeout, async {
        sqlx::query("SELECT 1").execute(&state.neon_db).await
    });
    let crm_probe = tokio::time::timeout(timeout, state.crm.ping());
    let (neon_result, crm_result) = tokio::join!(neon_probe, crm_probe);
    let ready = matches!(neon_result, Ok(Ok(_))) && matches!(crm_result, Ok(Ok(())));

    if ready {
        Ok((StatusCode::OK, Json(json!({"status": "ready"}))))
    } else {
        Ok((
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"status": "not_ready"})),
        ))
    }
}

pub async fn dashboard(
    State(state): State<AppState>,
    Extension(claims): Extension<DashboardAuthClaims>,
) -> Result<Json<DashboardResponse>, ApiError> {
    let franchise_id = scoped_franchise(&claims)?;
    let students = merged_students(&state, franchise_id, None).await?;
    let jobs = queries::list_current_jobs(&state.neon_db, Some(franchise_id))
        .await?
        .into_iter()
        .map(PublicJob::from)
        .collect();
    Ok(Json(DashboardResponse { students, jobs }))
}

pub async fn dashboard_health(
    State(state): State<AppState>,
    Extension(claims): Extension<DashboardAuthClaims>,
) -> Result<(StatusCode, Json<DashboardHealthResponse>), ApiError> {
    if claims.franchise_id.is_some()
        || claims.role.as_deref() != Some("health")
        || claims.user.as_deref().is_none_or(str::is_empty)
    {
        return Err(ApiError::Unauthorized);
    }

    let mut errors = Vec::new();
    let crm_students = match state.crm.list_students(None, None).await {
        Ok(students) => Some(students),
        Err(_) => {
            errors.push(AdminHealthErrorCategory::CrmUnavailable);
            None
        }
    };
    let jobs = match queries::list_current_jobs(&state.neon_db, None).await {
        Ok(jobs) => jobs.into_iter().map(PublicJob::from).collect(),
        Err(_) => {
            errors.push(AdminHealthErrorCategory::JobsUnavailable);
            Vec::new()
        }
    };

    let mut franchises: BTreeMap<i32, FranchiseHealthCounts> = BTreeMap::new();
    if let Some(students) = crm_students {
        let eligible_ids: Vec<i64> = students
            .iter()
            .filter(|student| student.is_grade_portal_eligible())
            .map(|student| student.crmstudentid)
            .collect();
        let states = if eligible_ids.is_empty() {
            Some(Default::default())
        } else {
            match queries::states_by_crm_ids(&state.neon_db, &eligible_ids).await {
                Ok(states) => Some(states),
                Err(_) => {
                    errors.push(AdminHealthErrorCategory::StateUnavailable);
                    None
                }
            }
        };
        for student in students {
            let counts =
                franchises
                    .entry(student.franchiseid)
                    .or_insert_with(|| FranchiseHealthCounts {
                        franchise_id: student.franchiseid,
                        franchise_name: student.franchise_name.clone(),
                        total_students: 0,
                        eligible_students: 0,
                        tracked_students: 0,
                        synced_students: 0,
                    });
            counts.total_students += 1;
            if student.is_grade_portal_eligible() {
                counts.eligible_students += 1;
                if let Some(student_state) = states
                    .as_ref()
                    .and_then(|states| states.get(&student.crmstudentid))
                {
                    counts.tracked_students += 1;
                    if student_state.status.as_deref() == Some("synced") {
                        counts.synced_students += 1;
                    }
                }
            }
        }
    }

    let status = if errors.is_empty() { "ok" } else { "degraded" };
    Ok((
        StatusCode::OK,
        Json(DashboardHealthResponse {
            status,
            franchises: franchises.into_values().collect(),
            jobs,
            errors,
            checked_at: chrono::Utc::now(),
        }),
    ))
}

pub async fn scheduler_job(
    State(state): State<AppState>,
    Extension(claims): Extension<SchedulerAuthClaims>,
    Json(payload): Json<SchedulerJobRequest>,
) -> Result<Json<PublicJob>, ApiError> {
    authorize_scheduler_job(&claims, &payload)?;
    let job = queries::create_scheduler_job(&state.neon_db, &claims.scheduler_id, &payload).await?;
    Ok(Json(PublicJob::from(job)))
}

pub async fn scheduler_reconcile_students(
    State(state): State<AppState>,
    Extension(claims): Extension<SchedulerAuthClaims>,
) -> Result<Json<ReconciliationSummary>, ApiError> {
    authorize_scheduler_reconcile(&claims)?;
    let students = state.crm.list_students(None, None).await?;
    let eligible_ids: Vec<i64> = students
        .iter()
        .filter(|student| student.is_grade_portal_eligible())
        .map(|student| student.crmstudentid)
        .collect();
    let (created_state, deleted_state) =
        queries::reconcile_student_state(&state.neon_db, &eligible_ids).await?;
    Ok(Json(ReconciliationSummary {
        canonical_students: students.len(),
        eligible_students: eligible_ids.len(),
        created_state,
        deleted_state,
        reconciled_at: chrono::Utc::now(),
    }))
}

pub async fn operator_put_alternate_credentials(
    State(state): State<AppState>,
    Extension(claims): Extension<OperatorAuthClaims>,
    Path(student_id): Path<i64>,
    Json(payload): Json<OperatorAlternateCredentialsRequest>,
) -> Result<StatusCode, ApiError> {
    let _operator_identity = &claims.operator_id;
    if student_id <= 0 {
        return Err(ApiError::BadRequest(
            "Student identifier must be positive".into(),
        ));
    }
    payload.validate()?;
    let canonical_students = state.crm.list_students(None, Some(student_id)).await?;
    let canonical_student = canonical_students
        .iter()
        .find(|student| student.crmstudentid == student_id)
        .ok_or(ApiError::NotFound)?;
    if !canonical_student.is_grade_portal_eligible() {
        return Err(ApiError::BadRequest(
            "Student is not eligible for synchronization".into(),
        ));
    }

    let credentials = payload.credentials();
    let envelope = state
        .config
        .alternate_credential_keyring
        .encrypt(student_id, &credentials)
        .map_err(|_| ApiError::Unavailable)?;
    debug_assert_eq!(
        envelope.key_id,
        state.config.alternate_credential_keyring.active_key_id()
    );
    queries::write_alternate_credentials(
        &state.neon_db,
        student_id,
        &payload.portal_url,
        &envelope,
    )
    .await?;
    Ok(StatusCode::NO_CONTENT)
}

pub async fn operator_delete_alternate_credentials(
    State(state): State<AppState>,
    Extension(claims): Extension<OperatorAuthClaims>,
    Path(student_id): Path<i64>,
) -> Result<StatusCode, ApiError> {
    let _operator_identity = &claims.operator_id;
    if student_id <= 0 {
        return Err(ApiError::BadRequest(
            "Student identifier must be positive".into(),
        ));
    }
    if !queries::clear_alternate_credentials(&state.neon_db, student_id).await? {
        return Err(ApiError::NotFound);
    }
    Ok(StatusCode::NO_CONTENT)
}

pub async fn operator_retarget_job(
    State(state): State<AppState>,
    Extension(claims): Extension<OperatorAuthClaims>,
    Path(job_id): Path<Uuid>,
    Json(payload): Json<OperatorRetargetJobRequest>,
) -> Result<Json<PublicJob>, ApiError> {
    authorize_operator_retarget(&state, &payload)?;
    let reason = payload.reason()?;
    let job = queries::retarget_queued_job(
        &state.neon_db,
        job_id,
        &payload.target_worker_id,
        &claims.operator_id,
        reason,
    )
    .await?;
    Ok(Json(PublicJob::from(job)))
}

pub async fn operator_cancel_job(
    State(state): State<AppState>,
    Extension(claims): Extension<OperatorAuthClaims>,
    Path(job_id): Path<Uuid>,
    Json(payload): Json<OperatorCancelJobRequest>,
) -> Result<Json<PublicJob>, ApiError> {
    payload.validate()?;
    let job = queries::cancel_queued_job(
        &state.neon_db,
        job_id,
        &claims.operator_id,
        payload.reason()?,
    )
    .await?;
    Ok(Json(PublicJob::from(job)))
}

pub async fn list_students(
    State(state): State<AppState>,
    Extension(claims): Extension<DashboardAuthClaims>,
    Query(query): Query<StudentQuery>,
) -> Result<Json<StudentsResponse>, ApiError> {
    let franchise_id = scoped_franchise(&claims)?;
    Ok(Json(StudentsResponse {
        students: merged_students(&state, franchise_id, query.student_id).await?,
    }))
}

pub async fn get_student(
    State(state): State<AppState>,
    Extension(claims): Extension<DashboardAuthClaims>,
    Path(student_id): Path<i64>,
) -> Result<Json<PublicStudent>, ApiError> {
    let franchise_id = scoped_franchise(&claims)?;
    let mut students = merged_students(&state, franchise_id, Some(student_id)).await?;
    students.pop().map(Json).ok_or(ApiError::NotFound)
}

pub async fn auth_login(
    State(state): State<AppState>,
    Json(payload): Json<AuthLoginRequest>,
) -> Result<Json<AuthLoginResponse>, ApiError> {
    let result = state
        .crm
        .login(&payload.username, &payload.password)
        .await?;
    Ok(Json(AuthLoginResponse {
        authenticated: result.authenticated,
        role: result.role,
        franchise_id: result.franchise_id,
        display_name: result.display_name,
    }))
}

pub async fn manual_pull(
    State(state): State<AppState>,
    Extension(claims): Extension<DashboardAuthClaims>,
    Json(payload): Json<ManualPullRequest>,
) -> Result<Json<ManualPullResponse>, ApiError> {
    let franchise_id = scoped_franchise(&claims)?;
    let created = queries::create_manual_pull_job(
        &state.neon_db,
        &payload,
        franchise_id,
        scoped_role(&claims),
        claims.user.as_deref(),
        manual_pull_target(&state),
    )
    .await?;
    Ok(Json(created))
}

pub async fn current_jobs(
    State(state): State<AppState>,
    Extension(claims): Extension<DashboardAuthClaims>,
) -> Result<Json<WorkerJobsResponse>, ApiError> {
    let jobs = queries::list_current_jobs(&state.neon_db, Some(scoped_franchise(&claims)?)).await?;
    Ok(Json(WorkerJobsResponse {
        jobs: jobs.into_iter().map(PublicJob::from).collect(),
    }))
}

pub async fn job_by_id(
    State(state): State<AppState>,
    Extension(claims): Extension<DashboardAuthClaims>,
    Path(job_id): Path<Uuid>,
) -> Result<Json<PublicJob>, ApiError> {
    let job = queries::get_job(&state.neon_db, job_id, Some(scoped_franchise(&claims)?)).await?;
    job.map(PublicJob::from).map(Json).ok_or(ApiError::NotFound)
}

pub async fn job_events(
    State(state): State<AppState>,
    Extension(claims): Extension<DashboardAuthClaims>,
    Path(job_id): Path<Uuid>,
) -> Result<Json<JobEventsResponse>, ApiError> {
    let franchise_id = scoped_franchise(&claims)?;
    queries::get_job(&state.neon_db, job_id, Some(franchise_id))
        .await?
        .ok_or(ApiError::NotFound)?;
    let events = queries::list_events(&state.neon_db, job_id)
        .await?
        .into_iter()
        .map(PublicJobEvent::from)
        .collect();
    Ok(Json(JobEventsResponse { events }))
}

#[derive(Debug, Deserialize)]
pub struct LatestResultsQuery {
    pub limit: Option<i64>,
}

pub async fn latest_results(
    State(state): State<AppState>,
    Extension(claims): Extension<DashboardAuthClaims>,
    Query(query): Query<LatestResultsQuery>,
) -> Result<Json<LatestResultsResponse>, ApiError> {
    let results = queries::latest_results(
        &state.neon_db,
        Some(scoped_franchise(&claims)?),
        query.limit.unwrap_or(25),
    )
    .await?
    .into_iter()
    .map(PublicJobResult::from)
    .collect();
    Ok(Json(LatestResultsResponse { results }))
}

pub async fn claim_job(
    State(state): State<AppState>,
    Extension(claims): Extension<WorkerAuthClaims>,
) -> Result<Response, ApiError> {
    match queries::claim_next_job(
        &state.neon_db,
        &claims.worker_id,
        state.config.worker_lease_seconds,
    )
    .await?
    {
        Some(job) => Ok(Json(job).into_response()),
        None => Ok(StatusCode::NO_CONTENT.into_response()),
    }
}

pub async fn job_context(
    State(state): State<AppState>,
    Extension(claims): Extension<WorkerAuthClaims>,
    Path(job_id): Path<Uuid>,
) -> Result<Json<WorkerJobContext>, ApiError> {
    let lease_token = active_worker_lease(&claims)?;
    let job =
        queries::get_running_job_for_worker(&state.neon_db, job_id, &claims.worker_id, lease_token)
            .await?
            .ok_or(ApiError::NotFound)?;
    if !worker_owns_running_job(&job, &claims.worker_id) {
        return Err(ApiError::NotFound);
    }
    let crm_students = state
        .crm
        .list_students(Some(job.franchise_id), job.student_id)
        .await?;
    let eligible_students: Vec<_> = crm_students
        .iter()
        .filter(|student| student.is_grade_portal_eligible())
        .collect();
    let eligible_ids: Vec<i64> = eligible_students
        .iter()
        .map(|student| student.crmstudentid)
        .collect();
    let state_by_id = if eligible_ids.is_empty() {
        Default::default()
    } else {
        queries::states_by_crm_ids(&state.neon_db, &eligible_ids).await?
    };
    let students = eligible_students
        .into_iter()
        .map(|student| {
            let student_state = state_by_id.get(&student.crmstudentid);
            let alternate_credentials =
                worker_alternate_credentials(student_state, &state.config, student.crmstudentid)?;
            Ok(crate::models::merge_worker_student(
                student,
                student_state,
                alternate_credentials.as_ref(),
            ))
        })
        .collect::<Result<Vec<_>, ApiError>>()?;

    Ok(Json(WorkerJobContext {
        job_id,
        kind: job.kind,
        franchise_id: job.franchise_id,
        student_id: job.student_id,
        students,
    }))
}

pub async fn heartbeat_job(
    State(state): State<AppState>,
    Extension(claims): Extension<WorkerAuthClaims>,
    Path(job_id): Path<Uuid>,
    Json(payload): Json<WorkerHeartbeatRequest>,
) -> Result<StatusCode, ApiError> {
    payload.validate()?;
    queries::update_heartbeat(
        &state.neon_db,
        job_id,
        &claims.worker_id,
        active_worker_lease(&claims)?,
        state.config.worker_lease_seconds,
        &payload,
    )
    .await?;
    Ok(StatusCode::ACCEPTED)
}

pub async fn event_job(
    State(state): State<AppState>,
    Extension(claims): Extension<WorkerAuthClaims>,
    Path(job_id): Path<Uuid>,
    Json(payload): Json<WorkerEventRequest>,
) -> Result<StatusCode, ApiError> {
    payload.validate()?;
    queries::create_event(
        &state.neon_db,
        job_id,
        &claims.worker_id,
        active_worker_lease(&claims)?,
        &payload,
    )
    .await?;
    Ok(StatusCode::ACCEPTED)
}

pub async fn results_job(
    State(state): State<AppState>,
    Extension(claims): Extension<WorkerAuthClaims>,
    Path(job_id): Path<Uuid>,
    Json(payload): Json<WorkerResultRequest>,
) -> Result<StatusCode, ApiError> {
    payload.validate()?;
    let lease_token = active_worker_lease(&claims)?;

    let job =
        queries::get_running_job_for_worker(&state.neon_db, job_id, &claims.worker_id, lease_token)
            .await?
            .ok_or(ApiError::NotFound)?;
    if !worker_owns_running_job(&job, &claims.worker_id) {
        return Err(ApiError::NotFound);
    }
    let result_student_id = payload.crmstudentid();
    let canonical_student = match Some(result_student_id) {
        Some(student_id) if job.student_id.is_none() || job.student_id == Some(student_id) => state
            .crm
            .list_students(Some(job.franchise_id), Some(student_id))
            .await?
            .into_iter()
            .find(|student| student.crmstudentid == student_id),
        _ => None,
    };
    if let WorkerResultStateAction::ApplyStudentState(student_id) = worker_result_state_action(
        job.student_id,
        Some(result_student_id),
        canonical_student.as_ref(),
    ) {
        queries::record_worker_result(
            &state.neon_db,
            job_id,
            &claims.worker_id,
            lease_token,
            &payload,
            WorkerResultStateAction::ApplyStudentState(student_id),
        )
        .await?;
    } else {
        queries::record_worker_result(
            &state.neon_db,
            job_id,
            &claims.worker_id,
            lease_token,
            &payload,
            WorkerResultStateAction::RecordOnly,
        )
        .await?;
    }
    Ok(StatusCode::ACCEPTED)
}

pub async fn complete_job(
    State(state): State<AppState>,
    Extension(claims): Extension<WorkerAuthClaims>,
    Path(job_id): Path<Uuid>,
    Json(payload): Json<WorkerCompletionRequest>,
) -> Result<StatusCode, ApiError> {
    let lease_token = active_worker_lease(&claims)?;
    let job =
        queries::get_running_job_for_worker(&state.neon_db, job_id, &claims.worker_id, lease_token)
            .await?
            .ok_or(ApiError::NotFound)?;
    if !worker_owns_running_job(&job, &claims.worker_id) {
        return Err(ApiError::NotFound);
    }
    payload.validate_completion_for_job(&job.kind)?;
    queries::mark_complete(
        &state.neon_db,
        job_id,
        &claims.worker_id,
        lease_token,
        payload,
    )
    .await?;
    Ok(StatusCode::NO_CONTENT)
}

pub async fn fail_job(
    State(state): State<AppState>,
    Extension(claims): Extension<WorkerAuthClaims>,
    Path(job_id): Path<Uuid>,
    Json(payload): Json<WorkerFailRequest>,
) -> Result<StatusCode, ApiError> {
    queries::mark_failed(
        &state.neon_db,
        job_id,
        &claims.worker_id,
        active_worker_lease(&claims)?,
        payload,
    )
    .await?;
    Ok(StatusCode::NO_CONTENT)
}

#[cfg(test)]
mod tests {
    use std::{future::Future, sync::Arc};

    use super::*;
    use axum::body::Body;
    use axum::http::Request;
    use axum::routing::post;
    use chrono::Utc;
    use http_body_util::BodyExt;
    use tower::ServiceExt;

    use crate::api_keys::{parse_basic_keyring_json, parse_scheduler_keyring_json};
    use crate::config::ApiConfig;
    use crate::state::AppState;
    use sha2::{Digest, Sha256};

    fn test_state(secret: &str) -> AppState {
        fn sha(raw: &str) -> String {
            hex::encode(Sha256::digest(raw.as_bytes()))
        }

        AppState::new_test(ApiConfig {
            neon_database_url: "postgres://localhost:5432/postgres".into(),
            crm_database_url:
                "server=localhost;Database=test;Uid=u;Pwd=p;TrustServerCertificate=yes;Encrypt=yes;"
                    .into(),
            worker_api_keyring: parse_basic_keyring_json(
                &serde_json::json!({
                    "worker-test": {
                        "keys": [
                            {
                                "key_id": "primary",
                                "sha256": sha("worker-secret"),
                                "expires_at": "2099-01-01T00:00:00Z"
                            }
                        ]
                    }
                })
                .to_string(),
                "worker",
            )
            .unwrap(),
            scheduler_api_keyring: parse_scheduler_keyring_json(
                &serde_json::json!({
                    "scheduler-test": {
                        "keys": [
                            {
                                "key_id": "primary",
                                "sha256": sha("scheduler-secret"),
                                "expires_at": "2099-01-01T00:00:00Z"
                            }
                        ],
                        "franchise_ids": [11],
                        "target_worker_ids": ["worker-test"],
                        "can_reconcile": false
                    }
                })
                .to_string(),
            )
            .unwrap(),
            operator_api_keyring: parse_basic_keyring_json(
                &serde_json::json!({
                    "operator-test": {
                        "keys": [
                            {
                                "key_id": "primary",
                                "sha256": sha("operator-secret"),
                                "expires_at": "2099-01-01T00:00:00Z"
                            }
                        ]
                    }
                })
                .to_string(),
                "operator",
            )
            .unwrap(),
            readiness_api_keyring: parse_basic_keyring_json(
                &serde_json::json!({
                    "readiness-test": {
                        "keys": [
                            {
                                "key_id": "primary",
                                "sha256": sha("readiness-secret"),
                                "expires_at": "2099-01-01T00:00:00Z"
                            }
                        ]
                    }
                })
                .to_string(),
                "readiness",
            )
            .unwrap(),
            default_worker_id: "worker-test".into(),
            worker_lease_seconds: 300,
            dashboard_hmac_max_age_seconds: 60,
            readiness_timeout_millis: 100,
            production_mode: false,
            api_bind_addr: "127.0.0.1:0".into(),
            dashboard_hmac_verification_keys: crate::config::DashboardHmacVerificationKeys {
                active: secret.into(),
                previous: None,
            },
            alternate_credential_keyring: crate::credentials::test_keyring(),
            allow_plaintext_alternate_credentials: false,
            rust_log: "info".into(),
        })
        .expect("test state")
    }

    fn student_state() -> crate::models::StudentGradeState {
        crate::models::StudentGradeState {
            uuid: Uuid::nil(),
            crmstudentid: 42,
            portal2: Some("https://agenda.example.test".into()),
            p2username: None,
            p2password: None,
            alternate_credentials_version: None,
            alternate_credentials_key_id: None,
            alternate_credentials_nonce: None,
            alternate_credentials_ciphertext: None,
            yearstart: None,
            yearend: None,
            weeklydata: None,
            portal: None,
            passwordgood: None,
            status: None,
            error_msg: None,
            track_agenda: Some(true),
            weekly_agenda: None,
            created_at: Utc::now(),
            updated_at: Utc::now(),
        }
    }

    fn scheduler_claims(
        franchise_ids: impl IntoIterator<Item = i32>,
        target_worker_ids: impl IntoIterator<Item = &'static str>,
        can_reconcile: bool,
    ) -> SchedulerAuthClaims {
        SchedulerAuthClaims {
            scheduler_id: "dev-alice".into(),
            key_id: "2026-07".into(),
            franchise_ids: Arc::new(franchise_ids.into_iter().collect()),
            target_worker_ids: Arc::new(
                target_worker_ids.into_iter().map(str::to_string).collect(),
            ),
            can_reconcile,
        }
    }

    fn scheduler_request(franchise_id: i32, target_worker_id: &str) -> SchedulerJobRequest {
        serde_json::from_value(serde_json::json!({
            "idempotency_key": Uuid::nil(),
            "kind": "grade",
            "franchise_id": franchise_id,
            "target_worker_id": target_worker_id,
        }))
        .unwrap()
    }

    #[test]
    fn scheduler_allows_scoped_franchise_and_target() {
        let claims = scheduler_claims([11], ["worker-test"], false);
        let request = scheduler_request(11, "worker-test");

        assert!(authorize_scheduler_job(&claims, &request).is_ok());
    }

    #[test]
    fn scheduler_rejects_unscoped_franchise() {
        let claims = scheduler_claims([11], ["worker-test"], false);
        let request = scheduler_request(12, "worker-test");

        assert!(matches!(
            authorize_scheduler_job(&claims, &request),
            Err(ApiError::Forbidden)
        ));
    }

    #[test]
    fn scheduler_rejects_unscoped_target() {
        let claims = SchedulerAuthClaims {
            scheduler_id: "dev-alice".into(),
            key_id: "2026-07".into(),
            franchise_ids: Arc::new([11].into_iter().collect()),
            target_worker_ids: Arc::new(["dev-alice-laptop".into()].into_iter().collect()),
            can_reconcile: false,
        };
        let request: SchedulerJobRequest = serde_json::from_value(serde_json::json!({
            "idempotency_key": Uuid::nil(),
            "kind": "grade",
            "franchise_id": 11,
            "target_worker_id": "prod-windows-01"
        }))
        .unwrap();
        assert!(matches!(
            authorize_scheduler_job(&claims, &request),
            Err(ApiError::Forbidden)
        ));
    }

    #[test]
    fn scheduler_reconcile_requires_capability() {
        let denied = scheduler_claims([11], ["worker-test"], false);
        let allowed = scheduler_claims([11], ["worker-test"], true);

        assert!(matches!(
            authorize_scheduler_reconcile(&denied),
            Err(ApiError::Forbidden)
        ));
        assert!(authorize_scheduler_reconcile(&allowed).is_ok());
    }

    #[tokio::test]
    async fn manual_pull_uses_default_worker() {
        let state = test_state("manual-target-test");
        assert_eq!(manual_pull_target(&state), "worker-test");
    }

    #[tokio::test]
    async fn operator_retarget_requires_configured_worker() {
        let state = test_state("operator-retarget-test");
        let allowed = OperatorRetargetJobRequest {
            target_worker_id: "worker-test".into(),
            reason: "Move queued job to the local development worker".into(),
        };
        let denied = OperatorRetargetJobRequest {
            target_worker_id: "unknown-worker".into(),
            reason: "Move queued job to an unregistered worker".into(),
        };

        assert!(authorize_operator_retarget(&state, &allowed).is_ok());
        assert!(matches!(
            authorize_operator_retarget(&state, &denied),
            Err(ApiError::BadRequest(_))
        ));
    }

    #[test]
    fn operator_actions_require_nonblank_bounded_reason() {
        let blank = OperatorCancelJobRequest {
            reason: "   ".into(),
        };
        let oversized = OperatorRetargetJobRequest {
            target_worker_id: "worker-test".into(),
            reason: "x".repeat(257),
        };
        let valid = OperatorCancelJobRequest {
            reason: "No longer required".into(),
        };

        assert!(matches!(blank.validate(), Err(ApiError::BadRequest(_))));
        assert!(matches!(oversized.validate(), Err(ApiError::BadRequest(_))));
        assert!(valid.validate().is_ok());
    }

    #[tokio::test]
    async fn worker_prefers_encrypted_credentials_and_plaintext_fallback_is_explicit() {
        let state = test_state("worker-credential-test");
        let credentials = crate::credentials::AlternateCredentials {
            username: "encrypted-user".into(),
            password: "encrypted-password".into(),
        };
        let envelope = state
            .config
            .alternate_credential_keyring
            .encrypt(42, &credentials)
            .unwrap();
        let mut encrypted_state = student_state();
        encrypted_state.alternate_credentials_version = Some(envelope.version);
        encrypted_state.alternate_credentials_key_id = Some(envelope.key_id);
        encrypted_state.alternate_credentials_nonce = Some(envelope.nonce);
        encrypted_state.alternate_credentials_ciphertext = Some(envelope.ciphertext);
        encrypted_state.p2username = Some("legacy-user".into());
        encrypted_state.p2password = Some("legacy-password".into());

        let decrypted = worker_alternate_credentials(Some(&encrypted_state), &state.config, 42)
            .unwrap()
            .unwrap();
        assert_eq!(decrypted.username, "encrypted-user");

        let mut plaintext_state = student_state();
        plaintext_state.p2username = Some("legacy-user".into());
        plaintext_state.p2password = Some("legacy-password".into());
        assert!(
            worker_alternate_credentials(Some(&plaintext_state), &state.config, 42)
                .unwrap()
                .is_none()
        );
        let mut fallback_config = (*state.config).clone();
        fallback_config.allow_plaintext_alternate_credentials = true;
        let fallback = worker_alternate_credentials(Some(&plaintext_state), &fallback_config, 42)
            .unwrap()
            .unwrap();
        assert_eq!(fallback.username, "legacy-user");
    }

    #[tokio::test]
    async fn probes_separate_process_liveness_from_dependency_readiness() {
        let app = create_router(test_state("probe-hmac-secret"));

        let live = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri("/livez")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(live.status(), StatusCode::OK);
        let live_body = live.into_body().collect().await.unwrap().to_bytes();
        assert_eq!(
            serde_json::from_slice::<serde_json::Value>(&live_body).unwrap(),
            json!({"status": "ok"})
        );

        let old_health = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri("/health")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(old_health.status(), StatusCode::NOT_FOUND);

        for authorization in [None, Some("Bearer wrong-secret")] {
            let mut builder = Request::builder().uri("/readyz");
            if let Some(value) = authorization {
                builder = builder.header("authorization", value);
            }
            let response = app
                .clone()
                .oneshot(builder.body(Body::empty()).unwrap())
                .await
                .unwrap();
            assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
        }

        let unavailable = app
            .oneshot(
                Request::builder()
                    .uri("/readyz")
                    .header("authorization", "Bearer readiness-secret")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(unavailable.status(), StatusCode::SERVICE_UNAVAILABLE);
        let unavailable_body = unavailable.into_body().collect().await.unwrap().to_bytes();
        assert_eq!(
            serde_json::from_slice::<serde_json::Value>(&unavailable_body).unwrap(),
            json!({"status": "not_ready"})
        );
    }

    #[tokio::test]
    async fn readiness_uses_keyring_identity() {
        let app = create_router(test_state("readiness-keyring"));

        let wrong_role = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri("/readyz")
                    .header("authorization", "Bearer worker-secret")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(wrong_role.status(), StatusCode::UNAUTHORIZED);

        let readiness = app
            .oneshot(
                Request::builder()
                    .uri("/readyz")
                    .header("authorization", "Bearer readiness-secret")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(readiness.status(), StatusCode::SERVICE_UNAVAILABLE);
    }

    fn assert_worker_write_handler<F>(_: F)
    where
        F: Future<Output = Result<StatusCode, crate::error::ApiError>>,
    {
    }

    fn worker_claims() -> Extension<WorkerAuthClaims> {
        Extension(WorkerAuthClaims {
            worker_id: "worker-test".into(),
            key_id: "primary".into(),
            lease_token: Some(Uuid::nil()),
        })
    }

    #[tokio::test]
    async fn worker_write_handlers_return_query_and_crm_errors() {
        let state = test_state("worker-handler-signature");
        let job_id = Uuid::nil();

        assert_worker_write_handler(heartbeat_job(
            State(state.clone()),
            worker_claims(),
            Path(job_id),
            Json(
                serde_json::from_value(json!({
                    "kind": "grade",
                    "total": 1,
                    "attempted": 0,
                    "success": 0,
                    "errors": 0,
                }))
                .unwrap(),
            ),
        ));
        assert_worker_write_handler(event_job(
            State(state.clone()),
            worker_claims(),
            Path(job_id),
            Json(serde_json::from_value(json!({"code": "job_started"})).unwrap()),
        ));
        assert_worker_write_handler(results_job(
            State(state.clone()),
            worker_claims(),
            Path(job_id),
            Json(
                serde_json::from_value(json!({
                    "crmstudentid": 42,
                    "idempotency_key": "00000000-0000-0000-0000-000000000042",
                    "status": "synced",
                }))
                .unwrap(),
            ),
        ));
        assert_worker_write_handler(complete_job(
            State(state.clone()),
            worker_claims(),
            Path(job_id),
            Json(
                serde_json::from_value(json!({
                    "kind": "grade",
                    "total": 1,
                    "attempted": 1,
                    "success": 1,
                    "errors": 0,
                }))
                .unwrap(),
            ),
        ));
        assert_worker_write_handler(fail_job(
            State(state),
            worker_claims(),
            Path(job_id),
            Json(serde_json::from_value(json!({"code": "worker_failed"})).unwrap()),
        ));
    }

    #[tokio::test]
    async fn worker_auth_rejects_missing_token() {
        let state = test_state("hmac-secret");
        let app = create_router(state);
        let req = Request::builder()
            .uri("/api/worker/jobs/claim")
            .method("POST")
            .header("content-type", "application/json")
            .body(Body::from("{}"))
            .unwrap();

        let response = app.oneshot(req).await.unwrap();
        let status = response.status();
        let body = response.into_body().collect().await.unwrap().to_bytes();
        let payload: serde_json::Value = serde_json::from_slice(&body).unwrap();
        assert_eq!(status, StatusCode::UNAUTHORIZED);
        assert_eq!(payload["error"]["code"], "unauthorized");
    }

    #[tokio::test]
    async fn dashboard_student_write_routes_are_not_registered() {
        let app = create_router(test_state("read-only-dashboard"));
        for (method, uri) in [
            ("POST", "/api/students"),
            ("PATCH", "/api/students/42"),
            ("DELETE", "/api/students/42"),
        ] {
            let timestamp = Utc::now().timestamp().to_string();
            let nonce = Uuid::new_v4().to_string();
            let signature = crate::auth::compute_signature(
                "read-only-dashboard",
                crate::auth::DashboardSignatureInput {
                    timestamp: &timestamp,
                    method,
                    path_with_query: uri,
                    franchise_id: "11",
                    role: "2",
                    user: "test-user",
                    nonce: &nonce,
                    body: b"",
                },
            );
            let response = app
                .clone()
                .oneshot(
                    Request::builder()
                        .method(method)
                        .uri(uri)
                        .header("x-api-timestamp", timestamp)
                        .header("x-api-franchise-id", "11")
                        .header("x-api-role", "2")
                        .header("x-api-user", "test-user")
                        .header("x-api-nonce", nonce)
                        .header("x-api-signature", signature)
                        .body(Body::empty())
                        .unwrap(),
                )
                .await
                .unwrap();
            assert_eq!(
                response.status(),
                StatusCode::METHOD_NOT_ALLOWED,
                "{method} {uri}"
            );
        }
    }

    #[tokio::test]
    async fn scheduler_routes_require_a_scheduler_bearer_token() {
        let app = create_router(test_state("scheduler-auth"));
        for uri in ["/api/scheduler/jobs", "/api/scheduler/reconcile-students"] {
            let response = app
                .clone()
                .oneshot(
                    Request::builder()
                        .method("POST")
                        .uri(uri)
                        .header("content-type", "application/json")
                        .body(Body::from("{}"))
                        .unwrap(),
                )
                .await
                .unwrap();
            assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
        }
    }

    #[tokio::test]
    async fn operator_job_controls_require_an_operator_bearer_token() {
        let app = create_router(test_state("operator-job-auth"));
        for uri in [
            "/api/operator/jobs/00000000-0000-0000-0000-000000000000/retarget",
            "/api/operator/jobs/00000000-0000-0000-0000-000000000000/cancel",
        ] {
            let response = app
                .clone()
                .oneshot(
                    Request::builder()
                        .method("POST")
                        .uri(uri)
                        .header("content-type", "application/json")
                        .body(Body::from("{}"))
                        .unwrap(),
                )
                .await
                .unwrap();
            assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
        }
    }

    #[tokio::test]
    async fn forbidden_error_has_fixed_http_contract() {
        let response = ApiError::Forbidden.into_response();
        assert_eq!(response.status(), StatusCode::FORBIDDEN);
        let body = response.into_body().collect().await.unwrap().to_bytes();
        assert_eq!(
            serde_json::from_slice::<serde_json::Value>(&body).unwrap(),
            json!({"error": {"code": "forbidden", "message": "Forbidden"}})
        );
    }

    #[tokio::test]
    async fn dashboard_auth_accepts_valid_signature() {
        let state = test_state("session-secret");
        let probe = Router::new()
            .route("/probe", post(|| async { StatusCode::OK }))
            .route_layer(axum::middleware::from_fn_with_state(
                state.clone(),
                crate::auth::dashboard_auth_middleware,
            ))
            .with_state(state.clone());

        let timestamp = Utc::now().timestamp().to_string();
        let path = "/probe";
        let nonce = Uuid::new_v4().to_string();
        let body = serde_json::to_vec(&json!({ "kind": "grade" })).unwrap();
        let expected_sig = crate::auth::compute_signature(
            &state.config.dashboard_hmac_verification_keys.active,
            crate::auth::DashboardSignatureInput {
                timestamp: &timestamp,
                method: "POST",
                path_with_query: path,
                franchise_id: "",
                role: "",
                user: "test-user",
                nonce: &nonce,
                body: &body,
            },
        );

        let req = Request::builder()
            .uri(path)
            .method("POST")
            .header("x-api-timestamp", &timestamp)
            .header("x-api-franchise-id", "")
            .header("x-api-role", "")
            .header("x-api-user", "test-user")
            .header("x-api-nonce", nonce)
            .header("x-api-signature", expected_sig)
            .header("content-type", "application/json")
            .body(Body::from(body))
            .unwrap();

        let response = probe.oneshot(req).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
    }

    #[tokio::test]
    async fn dashboard_auth_signature_includes_path_and_query() {
        let state = test_state("session-secret");
        let probe = Router::new()
            .route("/probe", post(|| async { StatusCode::OK }))
            .route_layer(axum::middleware::from_fn_with_state(
                state.clone(),
                crate::auth::dashboard_auth_middleware,
            ))
            .with_state(state.clone());

        let timestamp = Utc::now().timestamp().to_string();
        let path = "/probe?franchise_id=11&sort=asc";
        let nonce = Uuid::new_v4().to_string();
        let body = serde_json::to_vec(&serde_json::json!({"kind":"grade"})).unwrap();
        let expected_sig = crate::auth::compute_signature(
            &state.config.dashboard_hmac_verification_keys.active,
            crate::auth::DashboardSignatureInput {
                timestamp: &timestamp,
                method: "POST",
                path_with_query: path,
                franchise_id: "11",
                role: "2",
                user: "alice",
                nonce: &nonce,
                body: &body,
            },
        );

        let req = Request::builder()
            .uri(path)
            .method("POST")
            .header("x-api-timestamp", &timestamp)
            .header("x-api-franchise-id", "11")
            .header("x-api-role", "2")
            .header("x-api-user", "alice")
            .header("x-api-nonce", nonce)
            .header("x-api-signature", expected_sig)
            .header("content-type", "application/json")
            .body(Body::from(body))
            .unwrap();

        let response = probe.oneshot(req).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
    }

    async fn safe_error_route() -> Result<StatusCode, crate::error::ApiError> {
        Err(crate::error::ApiError::Safe(
            "sanitized internal failure".into(),
        ))
    }

    #[tokio::test]
    async fn safe_error_response_hides_raw_database_detail() {
        let app = Router::new().route("/err", axum::routing::get(safe_error_route));
        let response = app
            .oneshot(Request::builder().uri("/err").body(Body::empty()).unwrap())
            .await
            .unwrap();
        let status = response.status();
        let body = response.into_body().collect().await.unwrap().to_bytes();
        let payload: serde_json::Value = serde_json::from_slice(&body).unwrap();
        assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR);
        assert_eq!(payload["error"]["code"], "internal_error");
        assert!(!payload.to_string().contains("password"));
    }

    #[test]
    fn merged_student_serialization_never_includes_passwords() {
        let crm = crate::models::CrmStudent {
            crmstudentid: 7,
            franchiseid: 5,
            firstname: "Ada".into(),
            lastname: "Lovelace".into(),
            grade: Some(12),
            portal1: Some("https://portal.example".into()),
            p1username: Some("ada".into()),
            p1password: Some("secret".into()),
            franchise_name: Some("Center".into()),
        };
        let student = crate::models::merge_public_student(&crm, None);
        let serialized = serde_json::to_value(student).unwrap();
        assert_eq!(serialized["crmstudentid"], 7);
        assert_eq!(serialized["grade_portal_eligible"], true);
        assert_eq!(serialized["has_portal1_username"], true);
        assert_eq!(serialized["has_portal1_password"], true);
        assert!(!serialized.to_string().contains("secret"));
        assert!(!serialized.to_string().contains("p1password"));
        assert!(!serialized.to_string().contains("p2password"));
    }

    #[test]
    fn merged_student_serialization_sanitizes_state_json_and_hides_raw_errors() {
        let crm = crate::models::CrmStudent {
            crmstudentid: 7,
            franchiseid: 5,
            firstname: "Ada".into(),
            lastname: "Lovelace".into(),
            grade: Some(12),
            portal1: Some("https://portal.example".into()),
            p1username: Some("ada".into()),
            p1password: Some("secret".into()),
            franchise_name: Some("Center".into()),
        };
        let state = crate::models::StudentGradeState {
            uuid: Uuid::nil(),
            crmstudentid: 7,
            portal2: None,
            p2username: None,
            p2password: None,
            alternate_credentials_version: None,
            alternate_credentials_key_id: None,
            alternate_credentials_nonce: None,
            alternate_credentials_ciphertext: None,
            yearstart: Some(2026),
            yearend: Some(2027),
            weeklydata: Some(serde_json::json!({
                "grade": 95,
                "tokenValue": "weekly-token",
                "nested": {"PASSWORD": "weekly-password", "keep": true},
                "items": [{"cookie": "weekly-cookie", "visible": "yes"}],
            })),
            portal: None,
            passwordgood: Some(true),
            status: Some("error".into()),
            error_msg: Some("raw persisted error detail".into()),
            track_agenda: Some(true),
            weekly_agenda: Some(serde_json::json!({
                "Monday": {"authorization": "agenda-auth", "task": "read"},
                "secret_notes": "agenda-secret",
                "items": [{"USERNAME": "agenda-user", "visible": true}],
            })),
            created_at: chrono::Utc::now(),
            updated_at: chrono::Utc::now(),
        };

        let serialized =
            serde_json::to_value(crate::models::merge_public_student(&crm, Some(&state))).unwrap();

        assert_eq!(
            serialized["weeklydata"],
            serde_json::json!({
                "grade": 95,
                "nested": {"keep": true},
                "items": [{"visible": "yes"}],
            })
        );
        assert_eq!(
            serialized["weekly_agenda"],
            serde_json::json!({
                "Monday": {"task": "read"},
                "items": [{"visible": true}],
            })
        );
        assert_eq!(
            serialized["error_msg"],
            "An error occurred while syncing this student."
        );
        for sensitive_value in [
            "weekly-token",
            "weekly-password",
            "weekly-cookie",
            "agenda-auth",
            "agenda-secret",
            "agenda-user",
            "raw persisted error detail",
        ] {
            assert!(!serialized.to_string().contains(sensitive_value));
        }
    }

    #[test]
    fn merged_student_serialization_marks_invalid_portal_credentials_ineligible_and_redacts_them() {
        for (portal1, p1username, p1password) in [
            (
                None,
                Some("portal-user-secret"),
                Some("portal-password-secret"),
            ),
            (
                Some(""),
                Some("portal-user-secret"),
                Some("portal-password-secret"),
            ),
            (
                Some(" \t "),
                Some("portal-user-secret"),
                Some("portal-password-secret"),
            ),
            (
                Some("https://portal.example"),
                None,
                Some("portal-password-secret"),
            ),
            (
                Some("https://portal.example"),
                Some(""),
                Some("portal-password-secret"),
            ),
            (
                Some("https://portal.example"),
                Some(" \n "),
                Some("portal-password-secret"),
            ),
            (
                Some("https://portal.example"),
                Some("portal-user-secret"),
                None,
            ),
            (
                Some("https://portal.example"),
                Some("portal-user-secret"),
                Some(""),
            ),
            (
                Some("https://portal.example"),
                Some("portal-user-secret"),
                Some("  "),
            ),
        ] {
            let crm = crate::models::CrmStudent {
                crmstudentid: 7,
                franchiseid: 5,
                firstname: "Ada".into(),
                lastname: "Lovelace".into(),
                grade: Some(12),
                portal1: portal1.map(str::to_owned),
                p1username: p1username.map(str::to_owned),
                p1password: p1password.map(str::to_owned),
                franchise_name: Some("Center".into()),
            };
            let serialized =
                serde_json::to_value(crate::models::merge_public_student(&crm, None)).unwrap();

            assert_eq!(serialized["grade_portal_eligible"], false);
            for credential_key in ["p1username", "p1password", "p2username", "p2password"] {
                assert!(serialized.get(credential_key).is_none());
            }
            assert!(!serialized.to_string().contains("portal-user-secret"));
            assert!(!serialized.to_string().contains("portal-password-secret"));
        }
    }
}
