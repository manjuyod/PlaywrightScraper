use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use sqlx::FromRow;
use uuid::Uuid;
use zeroize::{Zeroize, ZeroizeOnDrop};

use crate::credentials::{AlternateCredentials, EncryptedCredentialEnvelope};
use crate::error::ApiError;

#[derive(Clone)]
pub struct CrmStudent {
    pub crmstudentid: i64,
    pub franchiseid: i32,
    pub firstname: String,
    pub lastname: String,
    pub grade: Option<i32>,
    pub portal1: Option<String>,
    pub p1username: Option<String>,
    pub p1password: Option<String>,
    pub franchise_name: Option<String>,
}

impl CrmStudent {
    pub fn is_grade_portal_eligible(&self) -> bool {
        [
            self.portal1.as_deref(),
            self.p1username.as_deref(),
            self.p1password.as_deref(),
        ]
        .into_iter()
        .all(|value| value.is_some_and(|value| !value.trim().is_empty()))
    }
}

#[cfg(test)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CanonicalCrmStateAction {
    WriteState,
    DeleteState,
    NoStateMutation,
}

#[cfg(test)]
pub fn canonical_crm_state_action(student: Option<&CrmStudent>) -> CanonicalCrmStateAction {
    match student {
        Some(student) if student.is_grade_portal_eligible() => CanonicalCrmStateAction::WriteState,
        Some(_) => CanonicalCrmStateAction::DeleteState,
        None => CanonicalCrmStateAction::NoStateMutation,
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WorkerResultStateAction {
    RecordOnly,
    ApplyStudentState(i64),
}

pub fn worker_result_state_action(
    job_student_id: Option<i64>,
    result_student_id: Option<i64>,
    canonical_student: Option<&CrmStudent>,
) -> WorkerResultStateAction {
    let Some(result_student_id) = result_student_id else {
        return WorkerResultStateAction::RecordOnly;
    };
    if job_student_id.is_some_and(|student_id| student_id != result_student_id) {
        return WorkerResultStateAction::RecordOnly;
    }
    match canonical_student {
        Some(student)
            if student.crmstudentid == result_student_id && student.is_grade_portal_eligible() =>
        {
            WorkerResultStateAction::ApplyStudentState(result_student_id)
        }
        _ => WorkerResultStateAction::RecordOnly,
    }
}

#[cfg(test)]
mod tests {
    use chrono::{Duration, Utc};
    use uuid::Uuid;

    use super::{
        canonical_crm_state_action, lease_is_active, sanitize_public_json, worker_owns_running_job,
        worker_result_state_action, CanonicalCrmStateAction, CrmStudent, JobEvent,
        JobEventsResponse, JobResult, LatestResultsResponse, OperatorAlternateCredentialsRequest,
        PublicJob, PublicJobEvent, PublicJobResult, SchedulerJobKind, SchedulerJobRequest,
        WorkerClaimResponse, WorkerCompletionRequest, WorkerEventCode, WorkerEventRequest,
        WorkerFailRequest, WorkerHeartbeatRequest, WorkerJob, WorkerResultRequest,
        WorkerResultStateAction,
    };

    fn student_with_portal_credentials(
        portal1: Option<&str>,
        p1username: Option<&str>,
        p1password: Option<&str>,
    ) -> CrmStudent {
        CrmStudent {
            crmstudentid: 1,
            franchiseid: 1,
            firstname: "Ada".into(),
            lastname: "Lovelace".into(),
            grade: Some(12),
            portal1: portal1.map(str::to_owned),
            p1username: p1username.map(str::to_owned),
            p1password: p1password.map(str::to_owned),
            franchise_name: None,
        }
    }

    #[test]
    fn crm_student_is_grade_portal_eligible_with_all_nonblank_credentials() {
        let student = student_with_portal_credentials(
            Some("https://portal.example"),
            Some("ada"),
            Some("secret"),
        );

        assert!(student.is_grade_portal_eligible());
    }

    #[test]
    fn crm_student_is_grade_portal_eligible_rejects_missing_or_empty_required_fields() {
        for (portal1, p1username, p1password) in [
            (None, Some("ada"), Some("secret")),
            (Some("https://portal.example"), None, Some("secret")),
            (Some("https://portal.example"), Some("ada"), None),
            (Some(""), Some("ada"), Some("secret")),
            (Some("https://portal.example"), Some(""), Some("secret")),
            (Some("https://portal.example"), Some("ada"), Some("")),
        ] {
            let student = student_with_portal_credentials(portal1, p1username, p1password);

            assert!(!student.is_grade_portal_eligible());
        }
    }

    #[test]
    fn crm_student_is_grade_portal_eligible_rejects_whitespace_required_fields() {
        for (portal1, p1username, p1password) in [
            (Some(" \t "), Some("ada"), Some("secret")),
            (Some("https://portal.example"), Some(" \n "), Some("secret")),
            (Some("https://portal.example"), Some("ada"), Some("  ")),
        ] {
            let student = student_with_portal_credentials(portal1, p1username, p1password);

            assert!(!student.is_grade_portal_eligible());
        }
    }

    #[test]
    fn canonical_crm_state_action_uses_canonical_eligibility() {
        let eligible = student_with_portal_credentials(
            Some("https://portal.example"),
            Some("ada"),
            Some("secret"),
        );
        assert_eq!(
            canonical_crm_state_action(Some(&eligible)),
            CanonicalCrmStateAction::WriteState
        );

        for (portal1, p1username, p1password) in [
            (None, Some("ada"), Some("secret")),
            (Some("https://portal.example"), None, Some("secret")),
            (Some("https://portal.example"), Some("ada"), None),
            (Some(""), Some("ada"), Some("secret")),
            (Some("https://portal.example"), Some(""), Some("secret")),
            (Some("https://portal.example"), Some("ada"), Some("")),
            (Some(" \t "), Some("ada"), Some("secret")),
            (Some("https://portal.example"), Some(" \n "), Some("secret")),
            (Some("https://portal.example"), Some("ada"), Some("  ")),
        ] {
            let ineligible = student_with_portal_credentials(portal1, p1username, p1password);
            assert_eq!(
                canonical_crm_state_action(Some(&ineligible)),
                CanonicalCrmStateAction::DeleteState
            );
        }

        assert_eq!(
            canonical_crm_state_action(None),
            CanonicalCrmStateAction::NoStateMutation
        );
    }

    #[test]
    fn worker_result_state_action_requires_an_exact_eligible_canonical_student() {
        let mut eligible = student_with_portal_credentials(
            Some("https://portal.example"),
            Some("ada"),
            Some("secret"),
        );
        eligible.crmstudentid = 42;
        let mut ineligible = eligible.clone();
        ineligible.p1password = Some(" \t ".into());
        let mut different_student = eligible.clone();
        different_student.crmstudentid = 99;

        assert_eq!(
            worker_result_state_action(None, None, Some(&eligible)),
            WorkerResultStateAction::RecordOnly
        );
        assert_eq!(
            worker_result_state_action(Some(7), Some(42), Some(&eligible)),
            WorkerResultStateAction::RecordOnly
        );
        assert_eq!(
            worker_result_state_action(None, Some(42), None),
            WorkerResultStateAction::RecordOnly
        );
        assert_eq!(
            worker_result_state_action(None, Some(42), Some(&ineligible)),
            WorkerResultStateAction::RecordOnly
        );
        assert_eq!(
            worker_result_state_action(None, Some(42), Some(&different_student)),
            WorkerResultStateAction::RecordOnly
        );
        assert_eq!(
            worker_result_state_action(Some(42), Some(42), Some(&eligible)),
            WorkerResultStateAction::ApplyStudentState(42)
        );
    }

    #[test]
    fn sanitize_public_json_recursively_removes_sensitive_object_members() {
        let sanitized = sanitize_public_json(&serde_json::json!({
            "student": "Ada",
            "password": "root-password",
            "apiKey": "root-api-key",
            "credential": "root-credential",
            "auth": "root-auth",
            "nested": {
                "ClientSecret": "nested-secret",
                "accessToken": "nested-token",
                "private_key": "nested-private-key",
                "authHeader": "nested-auth-header",
                "keep": true,
            },
            "items": [
                {"Authorization": "Bearer value", "score": 95},
                {
                    "cookie_data": "session-cookie",
                    "SessionId": "session-id",
                    "visible": "yes"
                },
                {"USERNAME": "ada", "task": "read"},
                "plain array value",
            ],
        }));

        assert_eq!(
            sanitized,
            serde_json::json!({
                "student": "Ada",
                "nested": {"keep": true},
                "items": [
                    {"score": 95},
                    {"visible": "yes"},
                    {"task": "read"},
                    "plain array value",
                ],
            })
        );
    }

    #[test]
    fn job_history_public_dtos_expose_only_safe_metadata() {
        let event = PublicJobEvent::from(JobEvent {
            id: 1,
            job_id: Uuid::nil(),
            level: "fatal".into(),
            message: "portal-password-secret".into(),
            payload: serde_json::json!({"api_key": "event-api-key"}),
            created_at: Utc::now(),
        });
        let result = PublicJobResult::from(JobResult {
            id: 2,
            job_id: Uuid::nil(),
            crmstudentid: Some(42),
            payload: serde_json::json!({"session_id": "result-session-id"}),
            created_at: Utc::now(),
        });

        let serialized = serde_json::to_value(serde_json::json!({
            "events": JobEventsResponse { events: vec![event] },
            "results": LatestResultsResponse { results: vec![result] },
        }))
        .unwrap();

        assert_eq!(serialized["events"]["events"][0]["has_message"], true);
        assert_eq!(serialized["events"]["events"][0]["has_payload"], true);
        assert_eq!(serialized["events"]["events"][0]["is_error"], true);
        assert_eq!(serialized["results"]["results"][0]["crmstudentid"], 42);
        assert_eq!(serialized["results"]["results"][0]["has_payload"], true);
        for forbidden_key in ["level", "message", "payload"] {
            assert!(serialized["events"]["events"][0]
                .get(forbidden_key)
                .is_none());
            assert!(serialized["results"]["results"][0]
                .get(forbidden_key)
                .is_none());
        }
        for secret in [
            "portal-password-secret",
            "event-api-key",
            "result-session-id",
        ] {
            assert!(!serialized.to_string().contains(secret));
        }
    }

    #[test]
    fn worker_summary_contract_rejects_unknown_negative_and_unbounded_values() {
        let summary = serde_json::json!({
            "kind": "grade",
            "total": 2,
            "attempted": 1,
            "success": 1,
            "errors": 0,
        });
        let heartbeat: WorkerHeartbeatRequest = serde_json::from_value(summary.clone()).unwrap();
        let completion: WorkerCompletionRequest = serde_json::from_value(summary).unwrap();
        assert_eq!(serde_json::to_value(heartbeat).unwrap()["kind"], "grade");
        assert_eq!(serde_json::to_value(completion).unwrap()["total"], 2);

        for invalid in [
            serde_json::json!({
                "kind": "grade",
                "total": 1,
                "attempted": 0,
                "success": 0,
                "errors": 0,
                "debug": "unsafe",
            }),
            serde_json::json!({
                "kind": "grade",
                "total": -1,
                "attempted": 0,
                "success": 0,
                "errors": 0,
            }),
            serde_json::json!({
                "kind": "grade",
                "total": 100_001,
                "attempted": 0,
                "success": 0,
                "errors": 0,
            }),
            serde_json::json!({
                "kind": "other",
                "total": 1,
                "attempted": 0,
                "success": 0,
                "errors": 0,
            }),
        ] {
            assert!(serde_json::from_value::<WorkerHeartbeatRequest>(invalid).is_err());
        }
    }

    #[test]
    fn worker_event_and_failure_contracts_reject_arbitrary_text_and_json() {
        let event: WorkerEventRequest = serde_json::from_value(serde_json::json!({
            "code": "student_started",
            "crmstudentid": 42,
        }))
        .unwrap();
        assert_eq!(event.code, WorkerEventCode::StudentStarted);
        assert_eq!(
            event.storage_payload(),
            serde_json::json!({"crmstudentid": 42})
        );
        assert_eq!(event.storage_message(), "Worker started a student scrape.");

        for invalid in [
            serde_json::json!({"code": "student_started", "message": "unsafe"}),
            serde_json::json!({"code": "student_started", "payload": {"unsafe": true}}),
            serde_json::json!({"code": "unknown"}),
        ] {
            assert!(serde_json::from_value::<WorkerEventRequest>(invalid).is_err());
        }

        assert!(
            serde_json::from_value::<WorkerFailRequest>(serde_json::json!({
                "code": "worker_failed"
            }))
            .is_ok()
        );
        assert!(
            serde_json::from_value::<WorkerFailRequest>(serde_json::json!({
                "error_msg": "portal password leaked"
            }))
            .is_err()
        );
    }

    #[test]
    fn worker_result_contract_canonicalizes_safe_data_and_rejects_unsafe_shapes() {
        let request: WorkerResultRequest = serde_json::from_value(serde_json::json!({
            "crmstudentid": 42,
            "idempotency_key": "00000000-0000-0000-0000-000000000042",
            "status": "synced",
            "passwordgood": true,
            "parsed_grades": {"math": {"score": 95}},
            "weekly_agenda": {"missing": []},
        }))
        .unwrap();
        request.validate().unwrap();
        assert_eq!(
            request.storage_payload(),
            serde_json::json!({
                "status": "synced",
                "passwordgood": true,
                "parsed_grades": {"math": {"score": 95}},
                "weekly_agenda": {"missing": []},
            })
        );

        for unsafe_key in ["id", "student_id", "error", "traceback"] {
            let invalid = serde_json::json!({
                "crmstudentid": 42,
                "idempotency_key": "00000000-0000-0000-0000-000000000042",
                "status": "synced",
                unsafe_key: "unsafe",
            });
            assert!(serde_json::from_value::<WorkerResultRequest>(invalid).is_err());
        }
        let invalid = serde_json::json!({
            "crmstudentid": 0,
            "idempotency_key": "00000000-0000-0000-0000-000000000042",
            "status": "synced",
        });
        assert!(serde_json::from_value::<WorkerResultRequest>(invalid).is_err());

        let sensitive: WorkerResultRequest = serde_json::from_value(serde_json::json!({
            "crmstudentid": 42,
            "idempotency_key": "00000000-0000-0000-0000-000000000042",
            "status": "synced",
            "parsed_grades": {"api_key": "unsafe"},
        }))
        .unwrap();
        assert!(sensitive.validate().is_err());

        for diagnostic_key in [
            "error",
            "errors",
            "exception",
            "traceback",
            "stack",
            "detail",
            "message",
        ] {
            let sensitive: WorkerResultRequest = serde_json::from_value(serde_json::json!({
                "crmstudentid": 42,
                "idempotency_key": "00000000-0000-0000-0000-000000000042",
                "status": "synced",
                "parsed_grades": {diagnostic_key: "unsafe"},
            }))
            .unwrap();
            assert!(sensitive.validate().is_err(), "{diagnostic_key}");
        }

        let safe_names: WorkerResultRequest = serde_json::from_value(serde_json::json!({
            "crmstudentid": 42,
            "idempotency_key": "00000000-0000-0000-0000-000000000042",
            "status": "synced",
            "parsed_grades": {"author": "safe", "monkey": "safe", "assignmentKey": "safe"},
        }))
        .unwrap();
        assert!(safe_names.validate().is_ok());
    }

    #[test]
    fn completion_and_event_contracts_enforce_job_semantics() {
        let completion: WorkerCompletionRequest = serde_json::from_value(serde_json::json!({
            "kind": "grade",
            "total": 2,
            "attempted": 2,
            "success": 1,
            "errors": 1,
        }))
        .unwrap();
        assert!(completion.validate_completion_for_job("grade").is_ok());
        assert!(completion.validate_completion_for_job("agenda").is_err());

        let incomplete: WorkerCompletionRequest = serde_json::from_value(serde_json::json!({
            "kind": "grade",
            "total": 2,
            "attempted": 1,
            "success": 1,
            "errors": 0,
        }))
        .unwrap();
        assert!(incomplete.validate_completion_for_job("grade").is_err());

        let job_started: WorkerEventRequest =
            serde_json::from_value(serde_json::json!({"code": "job_started"})).unwrap();
        assert!(job_started.validate().is_ok());
        let invalid_job_started: WorkerEventRequest = serde_json::from_value(serde_json::json!({
            "code": "job_started",
            "crmstudentid": 42,
        }))
        .unwrap();
        assert!(invalid_job_started.validate().is_err());
        let invalid_student_started: WorkerEventRequest =
            serde_json::from_value(serde_json::json!({"code": "student_started"})).unwrap();
        assert!(invalid_student_started.validate().is_err());
    }

    #[test]
    fn worker_lifecycle_access_requires_the_assigned_running_worker() {
        let job = WorkerJob {
            id: Uuid::nil(),
            kind: "grade".into(),
            status: "running".into(),
            franchise_id: 1,
            student_id: None,
            target_worker_id: Some("worker-a".into()),
            worker_id: Some("worker-a".into()),
            heartbeat: None,
            completed_payload: None,
            created_at: Some(Utc::now()),
            updated_at: None,
            started_at: None,
            completed_at: None,
        };
        assert!(worker_owns_running_job(&job, "worker-a"));
        assert!(!worker_owns_running_job(&job, "worker-b"));

        let complete_job = WorkerJob {
            status: "complete".into(),
            ..job
        };
        assert!(!worker_owns_running_job(&complete_job, "worker-a"));
    }

    #[test]
    fn worker_claim_response_includes_lease_without_leaking_it_to_dashboard_jobs() {
        let claim = WorkerClaimResponse {
            job_id: Uuid::nil(),
            kind: "grade".into(),
            franchise_id: 1,
            student_id: Some(42),
            lease_token: Uuid::from_u128(42),
            lease_expires_at: Utc::now(),
        };
        let claim_json = serde_json::to_value(claim).unwrap();
        assert_eq!(claim_json["lease_token"], Uuid::from_u128(42).to_string());
        assert!(claim_json.get("lease_expires_at").is_some());

        let dashboard_job = WorkerJob {
            id: Uuid::nil(),
            kind: "grade".into(),
            status: "running".into(),
            franchise_id: 1,
            student_id: None,
            target_worker_id: Some("worker-a".into()),
            worker_id: Some("worker-a".into()),
            heartbeat: None,
            completed_payload: None,
            created_at: None,
            updated_at: None,
            started_at: None,
            completed_at: None,
        };
        assert!(serde_json::to_value(PublicJob::from(dashboard_job))
            .unwrap()
            .get("lease_token")
            .is_none());
    }

    #[test]
    fn public_job_exposes_only_scope_timestamps_and_validated_progress() {
        let now = Utc::now();
        let internal = WorkerJob {
            id: Uuid::nil(),
            kind: "grade".into(),
            status: "running".into(),
            franchise_id: 19,
            student_id: Some(42),
            target_worker_id: Some("worker-a".into()),
            worker_id: Some("secret-worker-identity".into()),
            heartbeat: Some(serde_json::json!({
                "kind": "grade",
                "total": 3,
                "attempted": 2,
                "success": 1,
                "errors": 1
            })),
            completed_payload: None,
            created_at: Some(now),
            updated_at: Some(now),
            started_at: Some(now),
            completed_at: None,
        };
        let public = serde_json::to_value(PublicJob::from(internal)).unwrap();

        assert_eq!(public["id"], Uuid::nil().to_string());
        assert_eq!(public["scope"]["franchise_id"], 19);
        assert_eq!(public["scope"]["student_id"], 42);
        assert_eq!(public["progress"]["attempted"], 2);
        for forbidden in [
            "worker_id",
            "created_by",
            "heartbeat",
            "completed_payload",
            "payload",
            "lease_token",
        ] {
            assert!(public.get(forbidden).is_none(), "{forbidden}");
        }
    }

    #[test]
    fn scheduler_job_contract_requires_positive_scope_and_hashes_semantics() {
        let request: super::SchedulerJobRequest = serde_json::from_value(serde_json::json!({
            "idempotency_key": "00000000-0000-0000-0000-000000000042",
            "kind": "agenda",
            "franchise_id": 19,
            "student_id": 42,
            "target_worker_id": "worker-a"
        }))
        .unwrap();
        assert!(request.validate().is_ok());
        assert!(SchedulerJobRequest::target_worker_id_is_valid("worker-a"));
        assert_ne!(request.request_hash(), [0; 32]);

        for invalid in [
            serde_json::json!({
                "idempotency_key": Uuid::nil(),
                "kind": "grade",
                "franchise_id": 0,
                "target_worker_id": "worker-a"
            }),
            serde_json::json!({
                "idempotency_key": Uuid::nil(),
                "kind": "grade",
                "franchise_id": 1,
                "student_id": -1,
                "target_worker_id": "worker-a"
            }),
            serde_json::json!({
                "idempotency_key": Uuid::nil(),
                "kind": "grade",
                "franchise_id": 1,
                "student_id": 42,
                "target_worker_id": ""
            }),
            serde_json::json!({
                "idempotency_key": Uuid::nil(),
                "kind": "grade",
                "franchise_id": 1,
                "student_id": 42,
                "target_worker_id": " bad-target "
            }),
        ] {
            let request: super::SchedulerJobRequest = serde_json::from_value(invalid).unwrap();
            assert!(request.validate().is_err());
        }
    }

    #[test]
    fn scheduler_job_requires_target_worker() {
        let request: serde_json::Value = serde_json::json!({
            "idempotency_key": Uuid::nil(),
            "kind": "grade",
            "franchise_id": 11,
            "student_id": 42
        });
        assert!(serde_json::from_value::<SchedulerJobRequest>(request).is_err());
    }

    #[test]
    fn scheduler_job_accepts_configured_identity_syntax() {
        let request = SchedulerJobRequest {
            idempotency_key: Uuid::nil(),
            kind: SchedulerJobKind::Grade,
            franchise_id: 11,
            student_id: Some(42),
            target_worker_id: "Developer.Alice@Laptop".into(),
        };

        assert!(request.validate().is_ok());
    }

    #[test]
    fn scheduler_hash_covers_target_worker() {
        let first = SchedulerJobRequest {
            idempotency_key: Uuid::nil(),
            kind: SchedulerJobKind::Grade,
            franchise_id: 11,
            student_id: Some(42),
            target_worker_id: "dev-alice-laptop".into(),
        };
        let second = SchedulerJobRequest {
            idempotency_key: Uuid::nil(),
            kind: SchedulerJobKind::Grade,
            franchise_id: 11,
            student_id: Some(42),
            target_worker_id: "prod-windows-01".into(),
        };
        assert_ne!(first.request_hash(), second.request_hash());
    }

    #[test]
    fn worker_job_deserializes_target_worker() {
        let job: WorkerJob = serde_json::from_value(serde_json::json!({
            "id": Uuid::nil(),
            "kind": "grade",
            "status": "queued",
            "franchise_id": 11,
            "student_id": 42,
            "target_worker_id": "worker-a"
        }))
        .unwrap();
        assert_eq!(job.target_worker_id.as_deref(), Some("worker-a"));
    }

    #[test]
    fn operator_alternate_credentials_require_a_complete_nonblank_https_set() {
        let valid: OperatorAlternateCredentialsRequest =
            serde_json::from_value(serde_json::json!({
                "portal_url": "https://school.example.test/login",
                "username": "alternate-user",
                "password": "alternate-password"
            }))
            .unwrap();
        assert!(valid.validate().is_ok());

        for invalid in [
            serde_json::json!({
                "portal_url": "ftp://school.example.test/login",
                "username": "alternate-user",
                "password": "alternate-password"
            }),
            serde_json::json!({
                "portal_url": "https://school.example.test/login",
                "username": " ",
                "password": "alternate-password"
            }),
            serde_json::json!({
                "portal_url": "https://school.example.test/login",
                "username": "alternate-user",
                "password": "\t"
            }),
        ] {
            let request: OperatorAlternateCredentialsRequest =
                serde_json::from_value(invalid).unwrap();
            assert!(request.validate().is_err());
        }
    }

    #[test]
    fn lease_activity_is_strictly_before_its_expiry() {
        let now = Utc::now();
        assert!(lease_is_active(now + Duration::seconds(1), now));
        assert!(!lease_is_active(now, now));
        assert!(!lease_is_active(now - Duration::seconds(1), now));
    }
}

#[allow(dead_code)]
#[derive(Clone, FromRow)]
pub struct StudentGradeState {
    pub uuid: Uuid,
    pub crmstudentid: i64,
    pub portal2: Option<String>,
    pub p2username: Option<String>,
    pub p2password: Option<String>,
    pub alternate_credentials_version: Option<i16>,
    pub alternate_credentials_key_id: Option<String>,
    pub alternate_credentials_nonce: Option<Vec<u8>>,
    pub alternate_credentials_ciphertext: Option<Vec<u8>>,
    pub yearstart: Option<i32>,
    pub yearend: Option<i32>,
    pub weeklydata: Option<Value>,
    pub portal: Option<String>,
    pub passwordgood: Option<bool>,
    pub status: Option<String>,
    pub error_msg: Option<String>,
    pub track_agenda: Option<bool>,
    pub weekly_agenda: Option<Value>,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

impl StudentGradeState {
    pub fn encrypted_alternate_credentials(
        &self,
    ) -> Result<Option<EncryptedCredentialEnvelope>, ApiError> {
        match (
            self.alternate_credentials_version,
            self.alternate_credentials_key_id.as_ref(),
            self.alternate_credentials_nonce.as_ref(),
            self.alternate_credentials_ciphertext.as_ref(),
        ) {
            (None, None, None, None) => Ok(None),
            (Some(version), Some(key_id), Some(nonce), Some(ciphertext)) => {
                Ok(Some(EncryptedCredentialEnvelope {
                    version,
                    key_id: key_id.clone(),
                    nonce: nonce.clone(),
                    ciphertext: ciphertext.clone(),
                }))
            }
            _ => Err(ApiError::Unavailable),
        }
    }

    pub fn has_encrypted_alternate_credentials(&self) -> bool {
        matches!(self.encrypted_alternate_credentials(), Ok(Some(_)))
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct PublicStudent {
    pub crmstudentid: i64,
    pub franchiseid: i32,
    pub firstname: String,
    pub lastname: String,
    pub grade: Option<i32>,
    pub portal1: Option<String>,
    pub grade_portal_eligible: bool,
    pub has_portal1_username: bool,
    pub has_portal1_password: bool,
    pub portal2: Option<String>,
    pub has_portal2_username: bool,
    pub has_portal2_password: bool,
    pub yearstart: Option<i32>,
    pub yearend: Option<i32>,
    pub weeklydata: Value,
    pub portal: Option<String>,
    pub passwordgood: Option<bool>,
    pub status: Option<String>,
    pub error_msg: Option<String>,
    pub track_agenda: bool,
    pub weekly_agenda: Value,
    pub franchise_name: Option<String>,
}

#[derive(Clone, Serialize)]
pub struct WorkerStudent {
    pub crmstudentid: i64,
    pub franchiseid: i32,
    pub firstname: String,
    pub lastname: String,
    pub grade: Option<i32>,
    pub portal1: Option<String>,
    pub p1username: Option<String>,
    pub p1password: Option<String>,
    pub portal2: Option<String>,
    pub p2username: Option<String>,
    pub p2password: Option<String>,
    pub yearstart: Option<i32>,
    pub yearend: Option<i32>,
    pub weeklydata: Value,
    pub portal: Option<String>,
    pub passwordgood: Option<bool>,
    pub status: Option<String>,
    pub error_msg: Option<String>,
    pub track_agenda: bool,
    pub weekly_agenda: Value,
}

fn empty_json_object() -> Value {
    serde_json::json!({})
}

const SENSITIVE_PUBLIC_JSON_KEY_PARTS: [&str; 10] = [
    "password",
    "secret",
    "token",
    "authorization",
    "cookie",
    "username",
    "key",
    "credential",
    "session",
    "auth",
];

fn is_sensitive_public_json_key(key: &str) -> bool {
    let lowercase_key = key.to_lowercase();
    SENSITIVE_PUBLIC_JSON_KEY_PARTS
        .iter()
        .any(|part| lowercase_key.contains(part))
}

pub fn sanitize_public_json(value: &Value) -> Value {
    match value {
        Value::Object(entries) => Value::Object(
            entries
                .iter()
                .filter(|(key, _)| !is_sensitive_public_json_key(key))
                .map(|(key, value)| (key.clone(), sanitize_public_json(value)))
                .collect(),
        ),
        Value::Array(values) => Value::Array(values.iter().map(sanitize_public_json).collect()),
        _ => value.clone(),
    }
}

fn public_error_message(error_msg: Option<&str>) -> Option<String> {
    error_msg
        .filter(|value| !value.is_empty())
        .map(|_| "An error occurred while syncing this student.".to_string())
}

pub fn merge_public_student(crm: &CrmStudent, state: Option<&StudentGradeState>) -> PublicStudent {
    let has_encrypted_alternate_credentials =
        state.is_some_and(StudentGradeState::has_encrypted_alternate_credentials);
    PublicStudent {
        crmstudentid: crm.crmstudentid,
        franchiseid: crm.franchiseid,
        firstname: crm.firstname.clone(),
        lastname: crm.lastname.clone(),
        grade: crm.grade,
        portal1: crm.portal1.clone(),
        grade_portal_eligible: crm.is_grade_portal_eligible(),
        has_portal1_username: crm
            .p1username
            .as_ref()
            .is_some_and(|value| !value.trim().is_empty()),
        has_portal1_password: crm
            .p1password
            .as_ref()
            .is_some_and(|value| !value.trim().is_empty()),
        portal2: state.and_then(|row| row.portal2.clone()),
        has_portal2_username: has_encrypted_alternate_credentials
            || state
                .and_then(|row| row.p2username.as_ref())
                .is_some_and(|value| !value.trim().is_empty()),
        has_portal2_password: has_encrypted_alternate_credentials
            || state
                .and_then(|row| row.p2password.as_ref())
                .is_some_and(|value| !value.trim().is_empty()),
        yearstart: state.and_then(|row| row.yearstart),
        yearend: state.and_then(|row| row.yearend),
        weeklydata: state
            .and_then(|row| row.weeklydata.as_ref())
            .map(sanitize_public_json)
            .unwrap_or_else(empty_json_object),
        portal: state.and_then(|row| row.portal.clone()),
        passwordgood: state.and_then(|row| row.passwordgood),
        status: state.and_then(|row| row.status.clone()),
        error_msg: state.and_then(|row| public_error_message(row.error_msg.as_deref())),
        track_agenda: state.and_then(|row| row.track_agenda).unwrap_or(false),
        weekly_agenda: state
            .and_then(|row| row.weekly_agenda.as_ref())
            .map(sanitize_public_json)
            .unwrap_or_else(empty_json_object),
        franchise_name: crm.franchise_name.clone(),
    }
}

pub fn merge_worker_student(
    crm: &CrmStudent,
    state: Option<&StudentGradeState>,
    alternate_credentials: Option<&AlternateCredentials>,
) -> WorkerStudent {
    WorkerStudent {
        crmstudentid: crm.crmstudentid,
        franchiseid: crm.franchiseid,
        firstname: crm.firstname.clone(),
        lastname: crm.lastname.clone(),
        grade: crm.grade,
        portal1: crm.portal1.clone(),
        p1username: crm.p1username.clone(),
        p1password: crm.p1password.clone(),
        portal2: state.and_then(|row| row.portal2.clone()),
        p2username: alternate_credentials.map(|credentials| credentials.username.clone()),
        p2password: alternate_credentials.map(|credentials| credentials.password.clone()),
        yearstart: state.and_then(|row| row.yearstart),
        yearend: state.and_then(|row| row.yearend),
        weeklydata: state
            .and_then(|row| row.weeklydata.clone())
            .unwrap_or_else(empty_json_object),
        portal: state.and_then(|row| row.portal.clone()),
        passwordgood: state.and_then(|row| row.passwordgood),
        status: state.and_then(|row| row.status.clone()),
        error_msg: state.and_then(|row| row.error_msg.clone()),
        track_agenda: state.and_then(|row| row.track_agenda).unwrap_or(false),
        weekly_agenda: state
            .and_then(|row| row.weekly_agenda.clone())
            .unwrap_or_else(empty_json_object),
    }
}

#[derive(Debug, Serialize)]
pub struct DashboardResponse {
    pub students: Vec<PublicStudent>,
    pub jobs: Vec<PublicJob>,
}

#[derive(Debug, Serialize)]
pub struct StudentsResponse {
    pub students: Vec<PublicStudent>,
}

#[derive(Debug, Deserialize)]
pub struct StudentQuery {
    #[serde(default, rename = "student_id", alias = "studentId")]
    pub student_id: Option<i64>,
}

#[derive(Debug, Deserialize)]
pub struct ManualPullRequest {
    pub kind: Option<String>,
    #[serde(default, rename = "student_id", alias = "studentId")]
    pub student_id: Option<i64>,
}

#[derive(Debug, Serialize)]
pub struct ManualPullResponse {
    pub job_id: Uuid,
    pub status: String,
}

#[derive(Deserialize, Zeroize, ZeroizeOnDrop)]
#[serde(deny_unknown_fields)]
pub struct OperatorAlternateCredentialsRequest {
    pub portal_url: String,
    pub username: String,
    pub password: String,
}

impl OperatorAlternateCredentialsRequest {
    pub fn validate(&self) -> Result<(), ApiError> {
        if self.portal_url.trim() != self.portal_url
            || self.portal_url.len() > 2_048
            || self.username.trim().is_empty()
            || self.username.len() > 1_024
            || self.password.trim().is_empty()
            || self.password.len() > 4_096
        {
            return Err(ApiError::BadRequest(
                "Alternate credentials must be a complete valid set".into(),
            ));
        }
        let portal_url = url::Url::parse(&self.portal_url).map_err(|_| {
            ApiError::BadRequest("Alternate portal URL must be a valid HTTPS URL".into())
        })?;
        if portal_url.scheme() != "https"
            || portal_url.host_str().is_none()
            || !portal_url.username().is_empty()
            || portal_url.password().is_some()
        {
            return Err(ApiError::BadRequest(
                "Alternate portal URL must be a valid HTTPS URL".into(),
            ));
        }
        Ok(())
    }

    pub fn credentials(&self) -> AlternateCredentials {
        AlternateCredentials {
            username: self.username.clone(),
            password: self.password.clone(),
        }
    }
}

#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum SchedulerJobKind {
    Grade,
    Agenda,
}

impl SchedulerJobKind {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Grade => "grade",
            Self::Agenda => "agenda",
        }
    }
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SchedulerJobRequest {
    pub idempotency_key: Uuid,
    pub kind: SchedulerJobKind,
    pub franchise_id: i32,
    pub student_id: Option<i64>,
    #[serde(rename = "target_worker_id", alias = "targetWorkerId")]
    pub target_worker_id: String,
}

impl SchedulerJobRequest {
    fn target_worker_id_is_valid(value: &str) -> bool {
        !value.is_empty() && value.trim() == value
    }

    pub fn validate(&self) -> Result<(), ApiError> {
        if self.franchise_id <= 0 || self.student_id.is_some_and(|value| value <= 0) {
            return Err(ApiError::BadRequest(
                "Scheduler job scope must use positive identifiers".into(),
            ));
        }
        if !Self::target_worker_id_is_valid(&self.target_worker_id) {
            return Err(ApiError::BadRequest(
                "Scheduler job target worker must be a valid identifier".into(),
            ));
        }
        Ok(())
    }

    pub fn request_hash(&self) -> [u8; 32] {
        let canonical = format!(
            "{}\n{}\n{}\n{}",
            self.kind.as_str(),
            self.franchise_id,
            self.student_id
                .map(|value| value.to_string())
                .unwrap_or_default(),
            self.target_worker_id
        );
        Sha256::digest(canonical.as_bytes()).into()
    }
}

pub(crate) fn validated_operator_reason(reason: &str) -> Result<&str, ApiError> {
    let reason = reason.trim();
    if reason.is_empty() || reason.chars().count() > 256 {
        return Err(ApiError::BadRequest(
            "Operator reason must contain between 1 and 256 characters".into(),
        ));
    }
    Ok(reason)
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OperatorRetargetJobRequest {
    pub target_worker_id: String,
    pub reason: String,
}

impl OperatorRetargetJobRequest {
    pub fn validate(&self) -> Result<(), ApiError> {
        if self.target_worker_id.is_empty() || self.target_worker_id.trim() != self.target_worker_id
        {
            return Err(ApiError::BadRequest(
                "Target worker must be a valid identifier".into(),
            ));
        }
        validated_operator_reason(&self.reason)?;
        Ok(())
    }

    pub fn reason(&self) -> Result<&str, ApiError> {
        validated_operator_reason(&self.reason)
    }
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OperatorCancelJobRequest {
    pub reason: String,
}

impl OperatorCancelJobRequest {
    pub fn validate(&self) -> Result<(), ApiError> {
        validated_operator_reason(&self.reason)?;
        Ok(())
    }

    pub fn reason(&self) -> Result<&str, ApiError> {
        validated_operator_reason(&self.reason)
    }
}

#[derive(Debug, Serialize)]
pub struct ReconciliationSummary {
    pub canonical_students: usize,
    pub eligible_students: usize,
    pub created_state: u64,
    pub deleted_state: u64,
    pub reconciled_at: DateTime<Utc>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "snake_case")]
#[allow(clippy::enum_variant_names)]
pub enum AdminHealthErrorCategory {
    CrmUnavailable,
    StateUnavailable,
    JobsUnavailable,
}

#[derive(Debug, Serialize)]
pub struct FranchiseHealthCounts {
    pub franchise_id: i32,
    pub franchise_name: Option<String>,
    pub total_students: usize,
    pub eligible_students: usize,
    pub tracked_students: usize,
    pub synced_students: usize,
}

#[derive(Debug, Serialize)]
pub struct DashboardHealthResponse {
    pub status: &'static str,
    pub franchises: Vec<FranchiseHealthCounts>,
    pub jobs: Vec<PublicJob>,
    pub errors: Vec<AdminHealthErrorCategory>,
    pub checked_at: DateTime<Utc>,
}

#[derive(Deserialize, Zeroize, ZeroizeOnDrop)]
pub struct AuthLoginRequest {
    pub username: String,
    pub password: String,
}

#[derive(Debug, Serialize)]
pub struct AuthLoginResponse {
    pub authenticated: bool,
    #[serde(rename = "role")]
    pub role: Option<i32>,
    #[serde(rename = "franchise_id")]
    pub franchise_id: Option<i32>,
    #[serde(rename = "display_name")]
    pub display_name: Option<String>,
}

#[derive(Deserialize, FromRow)]
pub struct WorkerJob {
    pub id: Uuid,
    pub kind: String,
    pub status: String,
    pub franchise_id: i32,
    pub student_id: Option<i64>,
    pub target_worker_id: Option<String>,
    pub worker_id: Option<String>,
    pub heartbeat: Option<Value>,
    pub completed_payload: Option<Value>,
    pub created_at: Option<DateTime<Utc>>,
    pub updated_at: Option<DateTime<Utc>>,
    pub started_at: Option<DateTime<Utc>>,
    pub completed_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Serialize)]
pub struct PublicJobScope {
    pub franchise_id: i32,
    pub student_id: Option<i64>,
}

#[derive(Debug, Serialize)]
pub struct PublicJobProgress {
    pub total: u32,
    pub attempted: u32,
    pub success: u32,
    pub errors: u32,
}

#[derive(Debug, Serialize)]
pub struct PublicJob {
    pub id: Uuid,
    pub kind: String,
    pub status: String,
    pub scope: PublicJobScope,
    pub created_at: Option<DateTime<Utc>>,
    pub updated_at: Option<DateTime<Utc>>,
    pub started_at: Option<DateTime<Utc>>,
    pub completed_at: Option<DateTime<Utc>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub progress: Option<PublicJobProgress>,
}

impl From<WorkerJob> for PublicJob {
    fn from(job: WorkerJob) -> Self {
        let progress_value = if job.status == "complete" {
            job.completed_payload.as_ref().or(job.heartbeat.as_ref())
        } else {
            job.heartbeat.as_ref().or(job.completed_payload.as_ref())
        };
        let progress = progress_value
            .and_then(|value| serde_json::from_value::<WorkerArtifactSummary>(value.clone()).ok())
            .filter(|summary| {
                summary.validate().is_ok() && summary.kind.matches_job(job.kind.as_str())
            })
            .map(|summary| PublicJobProgress {
                total: summary.total.value(),
                attempted: summary.attempted.value(),
                success: summary.success.value(),
                errors: summary.errors.value(),
            });
        Self {
            id: job.id,
            kind: job.kind,
            status: job.status,
            scope: PublicJobScope {
                franchise_id: job.franchise_id,
                student_id: job.student_id,
            },
            created_at: job.created_at,
            updated_at: job.updated_at,
            started_at: job.started_at,
            completed_at: job.completed_at,
            progress,
        }
    }
}

#[derive(Debug, Serialize, FromRow)]
pub struct WorkerClaimResponse {
    pub job_id: Uuid,
    pub kind: String,
    pub franchise_id: i32,
    pub student_id: Option<i64>,
    pub lease_token: Uuid,
    pub lease_expires_at: DateTime<Utc>,
}

#[cfg(test)]
pub fn lease_is_active(lease_expires_at: DateTime<Utc>, now: DateTime<Utc>) -> bool {
    lease_expires_at > now
}

pub fn worker_owns_running_job(job: &WorkerJob, worker_id: &str) -> bool {
    job.status == "running" && job.worker_id.as_deref() == Some(worker_id)
}

#[derive(Debug, Serialize)]
pub struct WorkerJobsResponse {
    pub jobs: Vec<PublicJob>,
}

#[derive(Debug, Serialize)]
pub struct JobEventsResponse {
    pub events: Vec<PublicJobEvent>,
}

#[derive(Deserialize, FromRow)]
pub struct JobEvent {
    pub id: i64,
    pub job_id: Uuid,
    pub level: String,
    pub message: String,
    pub payload: Value,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Serialize)]
pub struct LatestResultsResponse {
    pub results: Vec<PublicJobResult>,
}

#[derive(Deserialize, FromRow)]
pub struct JobResult {
    pub id: i64,
    pub job_id: Uuid,
    pub crmstudentid: Option<i64>,
    pub payload: Value,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Serialize)]
pub struct PublicJobEvent {
    pub id: i64,
    pub job_id: Uuid,
    pub is_error: bool,
    pub has_message: bool,
    pub has_payload: bool,
    pub created_at: DateTime<Utc>,
}

impl From<JobEvent> for PublicJobEvent {
    fn from(event: JobEvent) -> Self {
        Self {
            id: event.id,
            job_id: event.job_id,
            is_error: matches!(
                event.level.trim().to_ascii_lowercase().as_str(),
                "error" | "fatal"
            ),
            has_message: !event.message.trim().is_empty(),
            has_payload: !event.payload.is_null(),
            created_at: event.created_at,
        }
    }
}

#[derive(Debug, Serialize)]
pub struct PublicJobResult {
    pub id: i64,
    pub job_id: Uuid,
    pub crmstudentid: Option<i64>,
    pub has_payload: bool,
    pub created_at: DateTime<Utc>,
}

impl From<JobResult> for PublicJobResult {
    fn from(result: JobResult) -> Self {
        Self {
            id: result.id,
            job_id: result.job_id,
            crmstudentid: result.crmstudentid,
            has_payload: !result.payload.is_null(),
            created_at: result.created_at,
        }
    }
}

#[derive(Serialize)]
pub struct WorkerJobContext {
    pub job_id: Uuid,
    pub kind: String,
    pub franchise_id: i32,
    pub student_id: Option<i64>,
    pub students: Vec<WorkerStudent>,
}

const MAX_WORKER_ARTIFACT_COUNTER: u32 = 100_000;

#[derive(Debug, Clone, Copy, Serialize, PartialEq, Eq)]
#[serde(transparent)]
pub struct WorkerArtifactCounter(u32);

impl WorkerArtifactCounter {
    pub fn value(self) -> u32 {
        self.0
    }
}

impl<'de> Deserialize<'de> for WorkerArtifactCounter {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        let value = u32::deserialize(deserializer)?;
        if value > MAX_WORKER_ARTIFACT_COUNTER {
            return Err(serde::de::Error::custom(
                "worker counter exceeds the maximum",
            ));
        }
        Ok(Self(value))
    }
}

#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum WorkerArtifactKind {
    Grade,
    Agenda,
}

impl WorkerArtifactKind {
    pub fn matches_job(self, job_kind: &str) -> bool {
        matches!(
            (self, job_kind),
            (Self::Grade, "grade") | (Self::Agenda, "agenda")
        )
    }
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct WorkerArtifactSummary {
    pub kind: WorkerArtifactKind,
    pub total: WorkerArtifactCounter,
    pub attempted: WorkerArtifactCounter,
    pub success: WorkerArtifactCounter,
    pub errors: WorkerArtifactCounter,
}

impl WorkerArtifactSummary {
    pub fn validate(&self) -> Result<(), ApiError> {
        let total = self.total.value();
        let attempted = self.attempted.value();
        let success = self.success.value();
        let errors = self.errors.value();
        if attempted > total || success.saturating_add(errors) > attempted {
            return Err(ApiError::BadRequest(
                "Worker artifact counters are inconsistent".into(),
            ));
        }
        Ok(())
    }

    pub fn validate_completion_for_job(&self, job_kind: &str) -> Result<(), ApiError> {
        self.validate()?;
        if !self.kind.matches_job(job_kind) {
            return Err(ApiError::BadRequest(
                "Worker completion kind does not match job".into(),
            ));
        }
        if self.attempted.value() != self.total.value()
            || self.success.value().saturating_add(self.errors.value()) != self.attempted.value()
        {
            return Err(ApiError::BadRequest(
                "Worker completion counters are incomplete".into(),
            ));
        }
        Ok(())
    }
}

pub type WorkerHeartbeatRequest = WorkerArtifactSummary;
pub type WorkerCompletionRequest = WorkerArtifactSummary;

#[derive(Debug, Clone, Copy, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum WorkerEventCode {
    JobStarted,
    StudentStarted,
}

impl WorkerEventCode {
    fn storage_message(self) -> &'static str {
        match self {
            Self::JobStarted => "Worker started job.",
            Self::StudentStarted => "Worker started a student scrape.",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CrmStudentId(i64);

impl CrmStudentId {
    pub fn value(self) -> i64 {
        self.0
    }
}

impl<'de> Deserialize<'de> for CrmStudentId {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        let value = i64::deserialize(deserializer)?;
        if value <= 0 {
            return Err(serde::de::Error::custom("crmstudentid must be positive"));
        }
        Ok(Self(value))
    }
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkerEventRequest {
    pub code: WorkerEventCode,
    pub crmstudentid: Option<CrmStudentId>,
}

impl WorkerEventRequest {
    pub fn validate(&self) -> Result<(), ApiError> {
        match (self.code, self.crmstudentid) {
            (WorkerEventCode::JobStarted, None) | (WorkerEventCode::StudentStarted, Some(_)) => {
                Ok(())
            }
            _ => Err(ApiError::BadRequest(
                "Worker event fields are inconsistent".into(),
            )),
        }
    }

    pub fn storage_level(&self) -> &'static str {
        "info"
    }

    pub fn storage_message(&self) -> &'static str {
        self.code.storage_message()
    }

    pub fn storage_payload(&self) -> Value {
        self.crmstudentid
            .map(|student_id| serde_json::json!({ "crmstudentid": student_id.value() }))
            .unwrap_or_else(|| serde_json::json!({}))
    }
}

#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum WorkerFailureCode {
    WorkerFailed,
    PortalFailure,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkerFailRequest {
    pub code: WorkerFailureCode,
}

impl WorkerFailRequest {
    pub fn storage_message(&self) -> &'static str {
        match self.code {
            WorkerFailureCode::WorkerFailed => "Worker execution failed.",
            WorkerFailureCode::PortalFailure => "Portal synchronization failed.",
        }
    }
}

#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum WorkerResultStatus {
    Synced,
    AgendaSynced,
    BadLogin,
    Failed,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkerResultRequest {
    pub crmstudentid: CrmStudentId,
    pub idempotency_key: Uuid,
    pub status: WorkerResultStatus,
    #[serde(default)]
    pub failure_code: Option<WorkerFailureCode>,
    #[serde(default)]
    pub passwordgood: Option<bool>,
    #[serde(default)]
    pub parsed_grades: Option<Value>,
    #[serde(default)]
    pub weekly_agenda: Option<Value>,
}

impl WorkerResultRequest {
    pub fn validate(&self) -> Result<(), ApiError> {
        let requires_failure_code = self.status == WorkerResultStatus::Failed;
        if self.failure_code.is_some() != requires_failure_code {
            return Err(ApiError::BadRequest(
                "Worker result failure code does not match status".into(),
            ));
        }
        if let Some(value) = &self.parsed_grades {
            validate_worker_result_json(value)?;
        }
        if let Some(value) = &self.weekly_agenda {
            validate_worker_result_json(value)?;
        }
        Ok(())
    }

    pub fn crmstudentid(&self) -> i64 {
        self.crmstudentid.value()
    }

    pub fn storage_payload(&self) -> Value {
        let mut payload = serde_json::Map::new();
        payload.insert(
            "status".into(),
            serde_json::to_value(self.status).expect("result status serializes"),
        );
        if let Some(code) = self.failure_code {
            payload.insert(
                "failure_code".into(),
                serde_json::to_value(code).expect("failure code serializes"),
            );
        }
        if let Some(passwordgood) = self.passwordgood {
            payload.insert("passwordgood".into(), Value::Bool(passwordgood));
        }
        if let Some(parsed_grades) = &self.parsed_grades {
            payload.insert("parsed_grades".into(), parsed_grades.clone());
        }
        if let Some(weekly_agenda) = &self.weekly_agenda {
            payload.insert("weekly_agenda".into(), weekly_agenda.clone());
        }
        Value::Object(payload)
    }

    pub fn storage_status(&self) -> &'static str {
        match self.status {
            WorkerResultStatus::Synced => "synced",
            WorkerResultStatus::AgendaSynced => "agenda_synced",
            WorkerResultStatus::BadLogin => "bad_login",
            WorkerResultStatus::Failed => "failed",
        }
    }

    pub fn failure_message(&self) -> Option<&'static str> {
        self.failure_code
            .map(|_| "Worker could not complete the student sync.")
    }
}

const MAX_WORKER_RESULT_DEPTH: usize = 8;
const MAX_WORKER_RESULT_NODES: usize = 1_000;
const MAX_WORKER_RESULT_STRING_BYTES: usize = 4_096;
const SENSITIVE_WORKER_RESULT_KEYS: &[&str] = &[
    "password",
    "p1password",
    "p2password",
    "secret",
    "clientsecret",
    "token",
    "accesstoken",
    "refreshtoken",
    "authorization",
    "authheader",
    "apikey",
    "privatekey",
    "credential",
    "credentials",
    "session",
    "sessionid",
    "cookie",
    "username",
    "p1username",
    "p2username",
    "error",
    "errors",
    "exception",
    "traceback",
    "stack",
    "detail",
    "message",
];

fn normalized_result_key(key: &str) -> String {
    key.chars()
        .filter(|character| character.is_alphanumeric())
        .flat_map(char::to_lowercase)
        .collect()
}

fn validate_worker_result_json(value: &Value) -> Result<(), ApiError> {
    fn visit(value: &Value, depth: usize, nodes: &mut usize) -> Result<(), ApiError> {
        if depth > MAX_WORKER_RESULT_DEPTH {
            return Err(ApiError::BadRequest(
                "Worker result JSON is too deeply nested".into(),
            ));
        }
        *nodes += 1;
        if *nodes > MAX_WORKER_RESULT_NODES {
            return Err(ApiError::BadRequest(
                "Worker result JSON is too large".into(),
            ));
        }
        match value {
            Value::String(text) if text.len() > MAX_WORKER_RESULT_STRING_BYTES => Err(
                ApiError::BadRequest("Worker result JSON string is too large".into()),
            ),
            Value::Object(object) => {
                for (key, nested) in object {
                    if SENSITIVE_WORKER_RESULT_KEYS.contains(&normalized_result_key(key).as_str()) {
                        return Err(ApiError::BadRequest(
                            "Worker result JSON contains a sensitive field".into(),
                        ));
                    }
                    visit(nested, depth + 1, nodes)?;
                }
                Ok(())
            }
            Value::Array(array) => {
                for nested in array {
                    visit(nested, depth + 1, nodes)?;
                }
                Ok(())
            }
            _ => Ok(()),
        }
    }

    visit(value, 0, &mut 0)
}
