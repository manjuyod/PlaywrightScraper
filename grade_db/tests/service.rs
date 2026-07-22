use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use chrono::{Duration, Utc};
use grade_db::error::AppError;
use grade_db::models::{
    deterministic_result_key, ActiveJob, CrmStudent, JobKind, JobLease, JobStartRequest,
    ResultOutcome, ResultPostRequest, StudentGradeState,
};
use grade_db::service::{BoundaryService, CrmGateway, NeonGateway, NeonResultWrite};
use serde_json::json;
use uuid::Uuid;

#[derive(Default)]
struct FakeCrm {
    students: Mutex<Vec<CrmStudent>>,
}

#[async_trait]
impl CrmGateway for FakeCrm {
    async fn ping(&self) -> Result<(), AppError> {
        Ok(())
    }

    async fn list_students(
        &self,
        franchise_id: Option<i32>,
        student_id: Option<i64>,
    ) -> Result<Vec<CrmStudent>, AppError> {
        Ok(self
            .students
            .lock()
            .unwrap()
            .iter()
            .filter(|row| franchise_id.is_none_or(|value| row.franchiseid == value))
            .filter(|row| student_id.is_none_or(|value| row.crmstudentid == value))
            .cloned()
            .collect())
    }
}

struct FakeNeon {
    states: Mutex<HashMap<i64, StudentGradeState>>,
    active_job: Mutex<Option<ActiveJob>>,
    writes: Mutex<Vec<NeonResultWrite>>,
}

impl Default for FakeNeon {
    fn default() -> Self {
        Self {
            states: Mutex::new(HashMap::new()),
            active_job: Mutex::new(None),
            writes: Mutex::new(Vec::new()),
        }
    }
}

#[async_trait]
impl NeonGateway for FakeNeon {
    async fn ping(&self) -> Result<(), AppError> {
        Ok(())
    }

    async fn ensure_states(&self, _crm_ids: &[i64]) -> Result<(), AppError> {
        Ok(())
    }

    async fn states_by_crm_ids(
        &self,
        crm_ids: &[i64],
    ) -> Result<HashMap<i64, StudentGradeState>, AppError> {
        let states = self.states.lock().unwrap();
        Ok(crm_ids
            .iter()
            .filter_map(|id| states.get(id).cloned().map(|row| (*id, row)))
            .collect())
    }

    async fn start_job(
        &self,
        request: &JobStartRequest,
        franchise_id: Option<i32>,
        _runner_id: &str,
        _lease_seconds: i64,
        _total: u32,
    ) -> Result<JobLease, AppError> {
        Ok(JobLease {
            job_id: Uuid::from_u128(19),
            lease_token: Uuid::from_u128(42),
            lease_expires_at: Utc::now() + Duration::minutes(10),
            kind: request.kind,
            franchise_id,
            student_id: request.student_id,
        })
    }

    async fn active_job(&self, _job_id: Uuid, _lease_token: Uuid) -> Result<ActiveJob, AppError> {
        self.active_job
            .lock()
            .unwrap()
            .clone()
            .ok_or(AppError::LeaseExpired)
    }

    async fn record_result(&self, write: NeonResultWrite) -> Result<bool, AppError> {
        self.writes.lock().unwrap().push(write);
        Ok(false)
    }
}

fn crm_student(id: i64, password: Option<&str>) -> CrmStudent {
    CrmStudent {
        crmstudentid: id,
        franchiseid: 19,
        firstname: format!("Student{id}"),
        lastname: "Example".into(),
        grade: Some(10),
        portal1: Some("https://portal.example/login".into()),
        p1username: Some(format!("user{id}")),
        p1password: password.map(str::to_owned),
    }
}

#[tokio::test]
async fn start_job_returns_only_eligible_students_and_preserves_gps_context() {
    let crm = Arc::new(FakeCrm::default());
    crm.students
        .lock()
        .unwrap()
        .extend([crm_student(1, Some("pw")), crm_student(2, None)]);
    let neon = Arc::new(FakeNeon::default());
    neon.states.lock().unwrap().insert(
        1,
        StudentGradeState {
            crmstudentid: 1,
            track_agenda: true,
            auth_type: Some("gps_pictograph".into()),
            auth_answers: json!(["cat", "moon"]),
            ..Default::default()
        },
    );
    let service = BoundaryService::new(crm, neon, "worker-a".into(), 600);

    let response = service
        .start_job(JobStartRequest {
            kind: JobKind::Grade,
            franchise_id: Some(19),
            student_id: None,
        })
        .await
        .unwrap();

    assert_eq!(response.students.len(), 1);
    assert_eq!(response.students[0].crmstudentid, 1);
    assert_eq!(response.students[0].auth_images, vec!["cat", "moon"]);
    assert_eq!(response.progress.total, 1);
}

#[tokio::test]
async fn exact_ineligible_student_starts_an_empty_franchise_scoped_job() {
    let crm = Arc::new(FakeCrm::default());
    crm.students.lock().unwrap().push(crm_student(2, None));
    let neon = Arc::new(FakeNeon::default());
    let service = BoundaryService::new(crm, neon, "worker-a".into(), 600);

    let response = service
        .start_job(JobStartRequest {
            kind: JobKind::Grade,
            franchise_id: None,
            student_id: Some(2),
        })
        .await
        .unwrap();

    assert!(response.students.is_empty());
    assert_eq!(response.progress.total, 0);
    assert_eq!(response.lease.franchise_id, Some(19));
}

#[tokio::test]
async fn agenda_job_returns_only_students_with_tracking_enabled() {
    let crm = Arc::new(FakeCrm::default());
    crm.students
        .lock()
        .unwrap()
        .extend([crm_student(1, Some("pw")), crm_student(2, Some("pw"))]);
    let neon = Arc::new(FakeNeon::default());
    neon.states.lock().unwrap().insert(
        2,
        StudentGradeState {
            crmstudentid: 2,
            track_agenda: true,
            ..Default::default()
        },
    );
    let service = BoundaryService::new(crm, neon, "worker-a".into(), 600);

    let response = service
        .start_job(JobStartRequest {
            kind: JobKind::Agenda,
            franchise_id: Some(19),
            student_id: None,
        })
        .await
        .unwrap();

    assert_eq!(response.students.len(), 1);
    assert_eq!(response.students[0].crmstudentid, 2);
}

#[tokio::test]
async fn result_rechecks_crm_and_redacts_payload_when_student_became_ineligible() {
    let crm = Arc::new(FakeCrm::default());
    crm.students.lock().unwrap().push(crm_student(1, None));
    let neon = Arc::new(FakeNeon::default());
    *neon.active_job.lock().unwrap() = Some(ActiveJob {
        job_id: Uuid::from_u128(19),
        lease_token: Uuid::from_u128(42),
        kind: JobKind::Grade,
        franchise_id: Some(19),
        student_id: None,
    });
    let service = BoundaryService::new(crm, neon.clone(), "worker-a".into(), 600);
    let request = ResultPostRequest {
        job_id: Uuid::from_u128(19),
        lease_token: Uuid::from_u128(42),
        crmstudentid: 1,
        outcome: ResultOutcome::GradeSuccess {
            parsed_grades: json!({"Algebra": 94}),
        },
    };

    let response = service.post_result(request).await.unwrap();

    assert!(!response.applied);
    assert_eq!(response.rejection_code.as_deref(), Some("crm_ineligible"));
    let writes = neon.writes.lock().unwrap();
    assert_eq!(writes.len(), 1);
    assert!(!writes[0].audit_payload.to_string().contains("Algebra"));
}

#[tokio::test]
async fn result_is_rejected_when_crm_no_longer_returns_the_student() {
    let crm = Arc::new(FakeCrm::default());
    let neon = Arc::new(FakeNeon::default());
    *neon.active_job.lock().unwrap() = Some(ActiveJob {
        job_id: Uuid::from_u128(19),
        lease_token: Uuid::from_u128(42),
        kind: JobKind::Grade,
        franchise_id: Some(19),
        student_id: None,
    });
    let service = BoundaryService::new(crm, neon.clone(), "worker-a".into(), 600);
    let request = ResultPostRequest {
        job_id: Uuid::from_u128(19),
        lease_token: Uuid::from_u128(42),
        crmstudentid: 1,
        outcome: ResultOutcome::GradeSuccess {
            parsed_grades: json!({"Algebra": 94}),
        },
    };

    let response = service.post_result(request).await.unwrap();

    assert!(!response.applied);
    assert_eq!(response.rejection_code.as_deref(), Some("crm_ineligible"));
    let writes = neon.writes.lock().unwrap();
    assert_eq!(writes.len(), 1);
    assert!(!writes[0].audit_payload.to_string().contains("Algebra"));
}

#[tokio::test]
async fn rejected_failure_uses_the_job_kind_for_its_idempotency_identity() {
    let crm = Arc::new(FakeCrm::default());
    crm.students.lock().unwrap().push(crm_student(1, None));
    let neon = Arc::new(FakeNeon::default());
    *neon.active_job.lock().unwrap() = Some(ActiveJob {
        job_id: Uuid::from_u128(19),
        lease_token: Uuid::from_u128(42),
        kind: JobKind::Grade,
        franchise_id: Some(19),
        student_id: None,
    });
    let service = BoundaryService::new(crm, neon.clone(), "worker-a".into(), 600);

    service
        .post_result(ResultPostRequest {
            job_id: Uuid::from_u128(19),
            lease_token: Uuid::from_u128(42),
            crmstudentid: 1,
            outcome: ResultOutcome::Failure {
                code: "bad_login".into(),
                passwordgood: Some(false),
            },
        })
        .await
        .unwrap();

    let writes = neon.writes.lock().unwrap();
    assert_eq!(
        writes[0].idempotency_key,
        deterministic_result_key(Uuid::from_u128(19), 1, "grade")
    );
}
