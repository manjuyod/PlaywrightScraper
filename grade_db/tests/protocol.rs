use grade_db::config::{normalize_postgres_url, parse_bool, parse_crm_address};
use grade_db::models::{
    JobCompleteRequest, JobFailRequest, JobHeartbeatRequest, JobKind, JobStartRequest, Progress,
    ResultOutcome,
};
use serde_json::json;
use uuid::Uuid;

#[test]
fn postgres_urls_are_normalized_for_sqlx_without_changing_other_values() {
    assert_eq!(
        normalize_postgres_url("postgres://user:pw@host/db"),
        "postgresql://user:pw@host/db"
    );
    assert_eq!(
        normalize_postgres_url("postgresql://user:pw@host/db"),
        "postgresql://user:pw@host/db"
    );
    assert_eq!(
        normalize_postgres_url("postgresql+psycopg://user:pw@host/db"),
        "postgresql://user:pw@host/db"
    );
}

#[test]
fn crm_address_accepts_pyodbc_host_and_port_format() {
    assert_eq!(
        parse_crm_address("tcp:crm.example.test,1444").unwrap(),
        ("crm.example.test".into(), 1444)
    );
    assert_eq!(
        parse_crm_address("crm.example.test").unwrap(),
        ("crm.example.test".into(), 1433)
    );
    assert!(parse_crm_address(" ").is_err());
}

#[test]
fn boolean_flags_accept_only_documented_values() {
    assert!(parse_bool("1").unwrap());
    assert!(parse_bool("yes").unwrap());
    assert!(!parse_bool("false").unwrap());
    assert!(parse_bool("sometimes").is_err());
}

#[test]
fn job_start_validates_kind_and_positive_scope() {
    let request = JobStartRequest {
        kind: JobKind::Grade,
        franchise_id: Some(19),
        student_id: None,
    };
    assert!(request.validate().is_ok());

    assert!(JobStartRequest {
        franchise_id: Some(0),
        ..request.clone()
    }
    .validate()
    .is_err());
    assert!(JobStartRequest {
        student_id: Some(-1),
        ..request
    }
    .validate()
    .is_err());
}

#[test]
fn progress_counts_must_be_internally_consistent() {
    assert!(Progress {
        total: 3,
        attempted: 2,
        success: 1,
        errors: 1
    }
    .validate()
    .is_ok());
    assert!(Progress {
        total: 2,
        attempted: 3,
        success: 2,
        errors: 1
    }
    .validate()
    .is_err());
    assert!(Progress {
        total: 3,
        attempted: 2,
        success: 2,
        errors: 1
    }
    .validate()
    .is_err());
}

#[test]
fn result_outcome_must_match_the_job_kind() {
    let grade = ResultOutcome::GradeSuccess {
        parsed_grades: json!({"Math": 90}),
    };
    let agenda = ResultOutcome::AgendaSuccess {
        weekly_agenda: json!({"Monday": []}),
    };
    let failure = ResultOutcome::Failure {
        code: "bad_login".into(),
        passwordgood: Some(false),
    };

    assert!(grade.validate_for_job(JobKind::Grade).is_ok());
    assert!(grade.validate_for_job(JobKind::Agenda).is_err());
    assert!(agenda.validate_for_job(JobKind::Agenda).is_ok());
    assert!(failure.validate_for_job(JobKind::Grade).is_ok());
    assert!(failure.validate_for_job(JobKind::Agenda).is_ok());
    assert!(ResultOutcome::Failure {
        code: "password was secret".into(),
        passwordgood: None,
    }
    .validate_for_job(JobKind::Grade)
    .is_err());

    for sensitive in ["password", "p2_username", "auth_answers", "token"] {
        assert!(ResultOutcome::GradeSuccess {
            parsed_grades: json!({sensitive: "must-not-persist"}),
        }
        .validate_for_job(JobKind::Grade)
        .is_err());
    }
}

#[test]
fn academic_result_json_is_bounded_before_it_can_be_persisted() {
    let mut nested = json!({"grade": 90});
    for _ in 0..10 {
        nested = json!({"course": nested});
    }

    assert!(ResultOutcome::GradeSuccess {
        parsed_grades: nested,
    }
    .validate_for_job(JobKind::Grade)
    .is_err());
}

#[test]
fn lifecycle_requests_validate_progress_and_safe_failure_codes() {
    let job_id = Uuid::new_v4();
    let lease_token = Uuid::new_v4();
    let progress = Progress {
        total: 2,
        attempted: 1,
        success: 1,
        errors: 0,
    };
    let completed = Progress {
        total: 2,
        attempted: 2,
        success: 1,
        errors: 1,
    };

    assert!(JobHeartbeatRequest {
        job_id,
        lease_token,
        progress
    }
    .validate()
    .is_ok());
    assert!(JobCompleteRequest {
        job_id,
        lease_token,
        progress: completed
    }
    .validate()
    .is_ok());
    assert!(JobFailRequest {
        job_id,
        lease_token,
        code: "crm_unavailable".into()
    }
    .validate()
    .is_ok());
    assert!(JobFailRequest {
        job_id,
        lease_token,
        code: "secret password was foo".into()
    }
    .validate()
    .is_err());
}
