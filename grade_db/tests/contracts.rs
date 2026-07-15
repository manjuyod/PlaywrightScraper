use grade_db::models::{
    deterministic_result_key, merge_runner_student, CrmStudent, ResultOutcome, ResultPostRequest,
    StudentGradeState,
};
use serde_json::json;
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
fn runner_context_merges_crm_identity_with_neon_owned_configuration() {
    let state = StudentGradeState {
        crmstudentid: 42,
        portal2: Some("https://agenda.example/login".into()),
        p2username: Some("agenda-user".into()),
        p2password: Some("agenda-secret".into()),
        portal: Some("canvas".into()),
        track_agenda: true,
        auth_type: Some("gps_pictograph".into()),
        auth_answers: json!(["cat", "tree", "moon"]),
        status: Some("never".into()),
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
    assert_eq!(merged.status.as_deref(), Some("never"));
    assert_eq!(merged.passwordgood, Some(true));
    assert_eq!(merged.auth_images, vec!["cat", "tree", "moon"]);
}

#[test]
fn result_identity_is_stable_per_job_student_and_kind() {
    let job_id = Uuid::parse_str("00000000-0000-0000-0000-000000000019").unwrap();

    let first = deterministic_result_key(job_id, 42, "grade");
    let retry = deterministic_result_key(job_id, 42, "grade");
    let agenda = deterministic_result_key(job_id, 42, "agenda");

    assert_eq!(first, retry);
    assert_ne!(first, agenda);
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
