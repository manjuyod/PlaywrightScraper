use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use chrono::{Duration, Utc};
use grade_db::error::AppError;
use grade_db::models::{
    deterministic_result_key, ActiveJob, CrmStudent, JobKind, JobLease, JobStartRequest,
    ResultChannel, ResultOutcome, ResultPostRequest, StudentGradeState,
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
        portal2: None,
        p2username: None,
        p2password: None,
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
async fn agenda_job_returns_all_grade_eligible_students_regardless_of_tracking() {
    for kind in [JobKind::Agenda, JobKind::Grade] {
        for (franchise_id, student_id, expected) in [
            (Some(19), None, vec![1_i64, 2, 3]),
            (None, Some(1), vec![1]),
            (None, Some(4), vec![]),
            (None, None, vec![1, 2, 3, 5]),
            (Some(20), None, vec![5]),
        ] {
            let crm = Arc::new(FakeCrm::default());
            let mut incomplete = crm_student(4, None);
            incomplete.portal2 = Some("https://canvas.example/login".into());
            incomplete.p2username = Some("secondary".into());
            incomplete.p2password = Some("secondary-secret".into());
            let mut outside = crm_student(5, Some("pw"));
            outside.franchiseid = 20;
            crm.students.lock().unwrap().extend([
                crm_student(1, Some("pw")),
                crm_student(2, Some("pw")),
                crm_student(3, Some("pw")),
                incomplete,
                outside,
            ]);
            let neon = Arc::new(FakeNeon::default());
            for (id, enabled) in [(1, false), (2, true)] {
                neon.states.lock().unwrap().insert(
                    id,
                    StudentGradeState {
                        crmstudentid: id,
                        track_agenda: enabled,
                        ..Default::default()
                    },
                );
            }
            let service = BoundaryService::new(crm, neon.clone(), "worker-a".into(), 600);
            let response = service
                .start_job(JobStartRequest {
                    kind,
                    franchise_id,
                    student_id,
                })
                .await
                .unwrap();
            assert_eq!(
                response
                    .students
                    .iter()
                    .map(|s| s.crmstudentid)
                    .collect::<Vec<_>>(),
                expected
            );
            assert_eq!(response.progress.total as usize, expected.len());
            assert_eq!(
                response.lease.franchise_id,
                if student_id.is_some() {
                    Some(19)
                } else {
                    franchise_id
                }
            );
            for student in &response.students {
                assert_eq!(student.track_agenda, student.crmstudentid == 2);
            }
            assert!(!neon.states.lock().unwrap().get(&1).unwrap().track_agenda);
            assert!(neon.states.lock().unwrap().get(&2).unwrap().track_agenda);
        }
    }
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
        portal: Some("canvas".into()),
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
        portal: Some("canvas".into()),
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
async fn rejected_failure_uses_its_channel_for_idempotency_identity() {
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
            portal: Some("canvas".into()),
            outcome: ResultOutcome::Failure {
                channel: ResultChannel::Grade,
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

#[tokio::test]
async fn agenda_pull_results_use_independent_slot_idempotency_keys() {
    let crm = Arc::new(FakeCrm::default());
    crm.students
        .lock()
        .unwrap()
        .push(crm_student(1, Some("pw")));
    let neon = Arc::new(FakeNeon::default());
    neon.states.lock().unwrap().insert(
        1,
        StudentGradeState {
            crmstudentid: 1,
            track_agenda: true,
            ..Default::default()
        },
    );
    *neon.active_job.lock().unwrap() = Some(ActiveJob {
        job_id: Uuid::from_u128(19),
        lease_token: Uuid::from_u128(42),
        kind: JobKind::Agenda,
        franchise_id: Some(19),
        student_id: None,
    });
    let service = BoundaryService::new(crm, neon.clone(), "worker-a".into(), 600);

    for (kind, channel) in [
        ("primary_agenda_success", "primary_agenda"),
        ("secondary_agenda_success", "secondary_agenda"),
    ] {
        let request = serde_json::from_value(json!({
            "job_id": Uuid::from_u128(19),
            "lease_token": Uuid::from_u128(42),
            "crmstudentid": 1,
            "outcome": {
                "kind": kind,
                "agenda": {"portal": "canvas", "weeks": {}}
            }
        }))
        .unwrap();
        service.post_result(request).await.unwrap();

        let writes = neon.writes.lock().unwrap();
        assert_eq!(
            writes.last().unwrap().idempotency_key,
            deterministic_result_key(Uuid::from_u128(19), 1, channel)
        );
    }

    let writes = neon.writes.lock().unwrap();
    assert_eq!(writes.len(), 2);
    assert_eq!(
        writes[0].idempotency_key,
        deterministic_result_key(Uuid::from_u128(19), 1, "primary_agenda")
    );
    assert_eq!(
        writes[1].idempotency_key,
        deterministic_result_key(Uuid::from_u128(19), 1, "secondary_agenda")
    );
    assert_ne!(writes[0].idempotency_key, writes[1].idempotency_key);
}

#[tokio::test]
async fn agenda_results_ignore_legacy_tracking_for_both_channels() {
    for stored_flag in [None, Some(false), Some(true)] {
        let crm = Arc::new(FakeCrm::default());
        crm.students
            .lock()
            .unwrap()
            .push(crm_student(1, Some("pw")));
        let neon = Arc::new(FakeNeon::default());
        if let Some(track_agenda) = stored_flag {
            neon.states.lock().unwrap().insert(
                1,
                StudentGradeState {
                    crmstudentid: 1,
                    track_agenda,
                    ..Default::default()
                },
            );
        }
        *neon.active_job.lock().unwrap() = Some(ActiveJob {
            job_id: Uuid::from_u128(19),
            lease_token: Uuid::from_u128(42),
            kind: JobKind::Agenda,
            franchise_id: Some(19),
            student_id: None,
        });
        let service = BoundaryService::new(crm, neon.clone(), "worker-a".into(), 600);
        for channel in ["primary_agenda", "secondary_agenda"] {
            for failure in [false, true, false] {
                let outcome = if failure {
                    json!({"kind": "failure", "channel": channel, "code": "scrape_failed"})
                } else {
                    json!({"kind": format!("{channel}_success"),
                           "agenda": {"portal": "canvas", "weeks": {}}})
                };
                let request = serde_json::from_value(json!({
                    "job_id": Uuid::from_u128(19), "lease_token": Uuid::from_u128(42),
                    "crmstudentid": 1, "outcome": outcome,
                }))
                .unwrap();
                let response = service.post_result(request).await.unwrap();
                assert!(response.applied);
                assert!(response.rejection_code.is_none());
                let writes = neon.writes.lock().unwrap();
                let write = writes.last().unwrap();
                assert!(write.applied);
                assert_eq!(
                    write.idempotency_key,
                    deterministic_result_key(Uuid::from_u128(19), 1, channel)
                );
                assert!(!write.audit_payload.to_string().contains("pw"));
            }
        }
    }
}

#[tokio::test]
async fn agenda_result_guards_remain_enforced_without_tracking() {
    for scenario in [
        "expired",
        "student_scope",
        "franchise_scope",
        "crm_missing",
        "crm_ineligible",
    ] {
        let crm = Arc::new(FakeCrm::default());
        if scenario != "crm_missing" {
            crm.students.lock().unwrap().push(crm_student(
                1,
                if scenario == "crm_ineligible" {
                    None
                } else {
                    Some("pw")
                },
            ));
        }
        let neon = Arc::new(FakeNeon::default());
        if scenario != "expired" {
            *neon.active_job.lock().unwrap() = Some(ActiveJob {
                job_id: Uuid::from_u128(19),
                lease_token: Uuid::from_u128(42),
                kind: JobKind::Agenda,
                franchise_id: Some(if scenario == "franchise_scope" {
                    20
                } else {
                    19
                }),
                student_id: if scenario == "student_scope" {
                    Some(2)
                } else {
                    None
                },
            });
        }
        let service = BoundaryService::new(crm, neon.clone(), "worker-a".into(), 600);
        let request = serde_json::from_value(json!({
            "job_id": Uuid::from_u128(19), "lease_token": Uuid::from_u128(42),
            "crmstudentid": 1, "outcome": {"kind": "primary_agenda_success",
                "agenda": {"portal": "canvas", "weeks": {}}},
        }))
        .unwrap();
        let response = service.post_result(request).await;
        if scenario == "expired" {
            assert!(matches!(response, Err(AppError::LeaseExpired)));
            assert!(neon.writes.lock().unwrap().is_empty());
        } else {
            let response = response.unwrap();
            assert!(!response.applied);
            assert_eq!(
                response.rejection_code.as_deref(),
                Some(if scenario == "student_scope" {
                    "job_scope_mismatch"
                } else {
                    "crm_ineligible"
                })
            );
            assert!(neon
                .writes
                .lock()
                .unwrap()
                .iter()
                .all(|write| !write.applied));
        }
    }
}
