use std::collections::HashMap;
use std::sync::Arc;

use async_trait::async_trait;
use serde_json::Value;
use uuid::Uuid;

use crate::error::AppError;
use crate::models::{
    deterministic_result_key, merge_runner_student, ActiveJob, CrmStudent, JobKind, JobLease,
    JobStartRequest, JobStartResponse, Progress, ResultPostRequest, ResultPostResponse,
    StudentGradeState,
};

#[async_trait]
pub trait CrmGateway: Send + Sync {
    async fn ping(&self) -> Result<(), AppError>;

    async fn list_students(
        &self,
        franchise_id: Option<i32>,
        student_id: Option<i64>,
    ) -> Result<Vec<CrmStudent>, AppError>;
}

#[derive(Debug, Clone)]
pub struct NeonResultWrite {
    pub request: ResultPostRequest,
    pub idempotency_key: Uuid,
    pub audit_payload: Value,
    pub applied: bool,
    pub rejection_code: Option<String>,
}

#[async_trait]
pub trait NeonGateway: Send + Sync {
    async fn ping(&self) -> Result<(), AppError>;
    async fn ensure_states(&self, crm_ids: &[i64]) -> Result<(), AppError>;
    async fn states_by_crm_ids(
        &self,
        crm_ids: &[i64],
    ) -> Result<HashMap<i64, StudentGradeState>, AppError>;
    async fn start_job(
        &self,
        request: &JobStartRequest,
        franchise_id: Option<i32>,
        runner_id: &str,
        lease_seconds: i64,
        total: u32,
    ) -> Result<JobLease, AppError>;
    async fn active_job(&self, job_id: Uuid, lease_token: Uuid) -> Result<ActiveJob, AppError>;
    async fn record_result(&self, write: NeonResultWrite) -> Result<bool, AppError>;
}

pub struct BoundaryService {
    crm: Arc<dyn CrmGateway>,
    neon: Arc<dyn NeonGateway>,
    runner_id: String,
    lease_seconds: i64,
}

impl BoundaryService {
    pub fn new(
        crm: Arc<dyn CrmGateway>,
        neon: Arc<dyn NeonGateway>,
        runner_id: String,
        lease_seconds: i64,
    ) -> Self {
        Self {
            crm,
            neon,
            runner_id,
            lease_seconds,
        }
    }

    pub async fn start_job(&self, request: JobStartRequest) -> Result<JobStartResponse, AppError> {
        request
            .validate()
            .map_err(|message| AppError::Validation(message.into()))?;
        let crm_students = self
            .crm
            .list_students(request.franchise_id, request.student_id)
            .await?;
        if request.student_id.is_some() && crm_students.is_empty() {
            return Err(AppError::Validation("CRM student was not found".into()));
        }
        let resolved_franchise_id = if request.student_id.is_some() {
            crm_students.first().map(|row| row.franchiseid)
        } else {
            request.franchise_id
        };
        let eligible: Vec<_> = crm_students
            .iter()
            .filter(|row| row.is_grade_portal_eligible())
            .collect();
        let eligible_ids: Vec<_> = eligible.iter().map(|row| row.crmstudentid).collect();
        self.neon.ensure_states(&eligible_ids).await?;
        let state_by_id = self.neon.states_by_crm_ids(&eligible_ids).await?;
        let students: Vec<_> = eligible
            .into_iter()
            .map(|row| merge_runner_student(row, state_by_id.get(&row.crmstudentid)))
            .filter(|row| request.kind != JobKind::Agenda || row.track_agenda)
            .collect();
        let total = u32::try_from(students.len()).map_err(|_| AppError::Internal)?;
        let lease = self
            .neon
            .start_job(
                &request,
                resolved_franchise_id,
                &self.runner_id,
                self.lease_seconds,
                total,
            )
            .await?;
        Ok(JobStartResponse {
            lease,
            progress: Progress {
                total,
                ..Default::default()
            },
            students,
        })
    }

    pub async fn post_result(
        &self,
        request: ResultPostRequest,
    ) -> Result<ResultPostResponse, AppError> {
        let job = self
            .neon
            .active_job(request.job_id, request.lease_token)
            .await?;
        if job
            .student_id
            .is_some_and(|value| value != request.crmstudentid)
        {
            return self
                .record_rejected(request, job.kind, "job_scope_mismatch")
                .await;
        }
        request
            .outcome
            .validate_for_job(job.kind)
            .map_err(|message| AppError::Validation(message.into()))?;

        let crm_student = self
            .crm
            .list_students(job.franchise_id, Some(request.crmstudentid))
            .await?
            .into_iter()
            .find(|row| row.crmstudentid == request.crmstudentid);
        let Some(crm_student) = crm_student.filter(CrmStudent::is_grade_portal_eligible) else {
            return self
                .record_rejected(request, job.kind, "crm_ineligible")
                .await;
        };
        if crm_student.franchiseid != job.franchise_id.unwrap_or(crm_student.franchiseid) {
            return self
                .record_rejected(request, job.kind, "job_scope_mismatch")
                .await;
        }
        if job.kind == JobKind::Agenda {
            let states = self.neon.states_by_crm_ids(&[request.crmstudentid]).await?;
            if !states
                .get(&request.crmstudentid)
                .is_some_and(|row| row.track_agenda)
            {
                return self
                    .record_rejected(request, job.kind, "agenda_not_enabled")
                    .await;
            }
        }

        let idempotency_key =
            deterministic_result_key(job.job_id, request.crmstudentid, job.kind.as_str());
        let audit_payload = request.audit_payload(true, None);
        let duplicate = self
            .neon
            .record_result(NeonResultWrite {
                request,
                idempotency_key,
                audit_payload,
                applied: true,
                rejection_code: None,
            })
            .await?;
        Ok(ResultPostResponse {
            applied: true,
            duplicate,
            rejection_code: None,
        })
    }

    async fn record_rejected(
        &self,
        request: ResultPostRequest,
        job_kind: JobKind,
        code: &str,
    ) -> Result<ResultPostResponse, AppError> {
        let idempotency_key =
            deterministic_result_key(request.job_id, request.crmstudentid, job_kind.as_str());
        let audit_payload = request.audit_payload(false, Some(code));
        let duplicate = self
            .neon
            .record_result(NeonResultWrite {
                request,
                idempotency_key,
                audit_payload,
                applied: false,
                rejection_code: Some(code.into()),
            })
            .await?;
        Ok(ResultPostResponse {
            applied: false,
            duplicate,
            rejection_code: Some(code.into()),
        })
    }
}
