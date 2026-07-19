use grade_db::crm::sql;

#[test]
fn crm_student_query_is_read_only_and_leaves_eligibility_to_rust() {
    let query = sql::LIST_STUDENTS.to_ascii_lowercase();
    for column in ["gradeportalurl", "gradeportaluser", "gradeportalpwd"] {
        assert!(query.contains(column));
    }
    assert!(query.contains("@p1 is null or s.franchiseid = @p1"));
    assert!(query.contains("@p2 is null or s.id = @p2"));
    assert!(!query.contains("nullif(ltrim(rtrim("));
    assert!(!query.contains(" insert "));
    assert!(!query.contains(" update "));
    assert!(!query.contains(" delete "));
}

#[test]
fn crm_doctor_query_is_read_only() {
    assert_eq!(sql::PING.trim().to_ascii_lowercase(), "select 1");
}
