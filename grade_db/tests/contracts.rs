use grade_db::models::{
    deterministic_result_key, merge_runner_student, CrmStudent, JobKind, ResultOutcome,
    ResultPostRequest, StudentGradeState,
};
use serde_json::{json, Value};
use uuid::Uuid;

fn crm_student() -> CrmStudent {
    CrmStudent {
        crmstudentid: 42,
        franchiseid: 19,
        firstname: "Ada".into(),
        lastname: "Lovelace".into(),
        grade: Some(12),
        portal1: Some("https://portal.example/login".into()),
        p1username: Some("ada".into()),
        p1password: Some("secret".into()),
        portal2: Some("https://agenda.example/login".into()),
        p2username: Some("agenda-user".into()),
        p2password: Some("agenda-secret".into()),
    }
}

#[test]
fn crm_eligibility_requires_all_three_nonblank_portal_fields() {
    assert!(crm_student().is_grade_portal_eligible());

    for field in ["portal1", "p1username", "p1password"] {
        let mut student = crm_student();
        match field {
            "portal1" => student.portal1 = Some("  ".into()),
            "p1username" => student.p1username = None,
            "p1password" => student.p1password = Some("\t".into()),
            _ => unreachable!(),
        }
        assert!(!student.is_grade_portal_eligible(), "field={field}");
    }
}

#[test]
fn crm_secondary_portal_is_optional_for_primary_eligibility() {
    let mut student = crm_student();
    student.portal2 = None;
    student.p2username = None;
    student.p2password = None;

    assert!(student.is_grade_portal_eligible());
}

#[test]
fn runner_context_merges_crm_owned_portals_with_neon_owned_configuration() {
    let state = StudentGradeState {
        crmstudentid: 42,
        portal: Some("canvas".into()),
        track_agenda: true,
        weeklydata: json!({
            "2026-08-03": {"OLD MARKETING": 91},
            "2026-08-10": {},
            "2026-08-17": {"MARKETING 1": 92, "ENGLISH 11": 88},
            "2026-08-24": {}
        }),
        auth_type: Some("gps_pictograph".into()),
        auth_answers: json!(["cat", "tree", "moon"]),
        grade_status: Some("never".into()),
        passwordgood: Some(true),
    };

    let merged = merge_runner_student(&crm_student(), Some(&state));

    assert_eq!(merged.crmstudentid, 42);
    assert_eq!(merged.franchiseid, 19);
    assert_eq!(merged.p1username.as_deref(), Some("ada"));
    assert_eq!(
        merged.portal2.as_deref(),
        Some("https://agenda.example/login")
    );
    assert_eq!(merged.p2username.as_deref(), Some("agenda-user"));
    assert!(merged.track_agenda);
    assert_eq!(
        merged.known_course_titles,
        vec!["ENGLISH 11", "MARKETING 1"]
    );
    assert_eq!(merged.grade_status.as_deref(), Some("never"));
    assert_eq!(merged.passwordgood, Some(true));
    assert_eq!(merged.auth_images, vec!["cat", "tree", "moon"]);
}

#[test]
fn result_identity_is_stable_per_job_student_and_kind() {
    let job_id = Uuid::parse_str("00000000-0000-0000-0000-000000000019").unwrap();

    let first = deterministic_result_key(job_id, 42, "grade");
    let retry = deterministic_result_key(job_id, 42, "grade");
    let primary_agenda = deterministic_result_key(job_id, 42, "primary_agenda");
    let secondary_agenda = deterministic_result_key(job_id, 42, "secondary_agenda");

    assert_eq!(first, retry);
    assert_ne!(first, primary_agenda);
    assert_ne!(primary_agenda, secondary_agenda);
}

#[test]
fn rejected_result_audit_never_contains_academic_payload() {
    let request = ResultPostRequest {
        job_id: Uuid::nil(),
        lease_token: Uuid::nil(),
        crmstudentid: 42,
        outcome: ResultOutcome::GradeSuccess {
            parsed_grades: json!({"Algebra": 94}),
        },
    };

    let audit = request.audit_payload(false, Some("crm_ineligible"));

    assert_eq!(audit["status"], "rejected");
    assert_eq!(audit["rejection_code"], "crm_ineligible");
    assert!(audit.get("parsed_grades").is_none());
    assert!(!audit.to_string().contains("Algebra"));
}

#[test]
fn agenda_result_accepts_one_portal_slot() {
    let outcome = ResultOutcome::PrimaryAgendaSuccess {
        agenda: json!({
            "portal": "canvas",
            "weeks": {
                "2026-08-10": {
                    "English 11": {
                        "missing": [],
                        "low_score": [],
                        "due": [{
                            "title": "Reading response",
                            "dueDate": "2026-08-16",
                            "dueTime": null
                        }]
                    }
                }
            }
        }),
    };

    assert_eq!(outcome.validate_for_job(JobKind::Agenda), Ok(()));
}

#[test]
fn agenda_result_accepts_exact_node_limit_and_rejects_one_over() {
    let allowed = ResultOutcome::PrimaryAgendaSuccess {
        agenda: json!({"nodes": vec![Value::Null; 998]}),
    };
    let too_large = ResultOutcome::PrimaryAgendaSuccess {
        agenda: json!({"nodes": vec![Value::Null; 999]}),
    };

    assert_eq!(allowed.validate_for_job(JobKind::Agenda), Ok(()));
    assert_eq!(
        too_large.validate_for_job(JobKind::Agenda),
        Err("result payload is too large")
    );
}
