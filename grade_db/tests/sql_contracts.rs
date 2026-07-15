use grade_db::neon::sql;

fn compact(value: &str) -> String {
    value.split_whitespace().collect::<Vec<_>>().join(" ")
}

#[test]
fn doctor_schema_check_is_read_only_and_checks_required_columns() {
    let query = sql::SCHEMA_READY.to_ascii_lowercase();
    assert!(query.contains("information_schema.columns"));
    for required in [
        "crmstudentid",
        "weeklydata",
        "lease_token",
        "lease_expires_at",
        "idempotency_key",
        "applied",
    ] {
        assert!(query.contains(required));
    }
    for mutation in ["insert ", "update ", "delete ", "alter ", "drop "] {
        assert!(!query.contains(mutation));
    }
}

#[test]
fn starts_are_serialized_and_global_scopes_conflict_with_franchise_scopes() {
    assert!(sql::LOCK_JOB_KIND.contains("pg_advisory_xact_lock"));
    assert!(sql::EXPIRE_JOBS.contains("lease_expires_at <= now()"));
    assert!(sql::EXPIRE_JOBS.contains("INSERT INTO grade_scrape_job_events"));
    let conflict = compact(sql::FIND_CONFLICT);
    assert!(conflict.contains("franchise_id IS NULL"));
    assert!(conflict.contains("$2::integer IS NULL"));
    assert!(conflict.contains("franchise_id = $2"));
}

#[test]
fn lifecycle_mutations_require_the_current_unexpired_lease() {
    for query in [
        sql::ACTIVE_JOB,
        sql::HEARTBEAT,
        sql::COMPLETE_JOB,
        sql::FAIL_JOB,
    ] {
        assert!(query.contains("lease_token"));
        assert!(query.contains("status = 'running'"));
        assert!(query.contains("lease_expires_at > now()"));
    }
}

#[test]
fn result_writes_are_idempotent_and_state_updates_are_separate() {
    assert!(sql::INSERT_RESULT.contains("ON CONFLICT"));
    assert!(sql::EXISTING_RESULT.contains("idempotency_key"));
    assert!(sql::APPLY_GRADE.contains("weeklydata"));
    assert!(sql::APPLY_GRADE.contains("jsonb_build_object"));
    assert!(sql::APPLY_GRADE.contains("date_trunc('week', now())"));
    assert!(sql::APPLY_AGENDA.contains("weekly_agenda"));
    assert!(!sql::APPLY_AGENDA.contains("||"));
    assert!(!sql::INSERT_RESULT.contains("weeklydata"));
    assert!(!sql::INSERT_RESULT.contains("weekly_agenda"));
}
