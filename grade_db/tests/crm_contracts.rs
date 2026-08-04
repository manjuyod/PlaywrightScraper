use grade_db::crm::sql;

#[test]
fn crm_student_query_is_read_only_and_leaves_eligibility_to_rust() {
    let query = sql::LIST_STUDENTS.to_ascii_lowercase();
    for column in ["gradeportalurl", "gradeportaluser", "gradeportalpwd"] {
        assert!(query.contains(column));
    }
    for column in ["url2", "url2username", "url2password"] {
        assert!(query.contains(column));
    }
    assert!(query.contains("left join dbo.tblstudentgradeportalsecondary as secondary"));
    assert!(query.contains("secondary.studentid = s.id"));
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

    let query = sql::SECONDARY_SCHEMA_READY.to_ascii_lowercase();
    assert!(query.contains("select top (0)"));
    assert!(query.contains("from dbo.tblstudentgradeportalsecondary"));
    for column in ["studentid", "url2", "url2username", "url2password"] {
        assert!(query.contains(column));
    }
    for mutation in ["insert ", "update ", "delete ", "alter ", "drop "] {
        assert!(!query.contains(mutation));
    }
}
