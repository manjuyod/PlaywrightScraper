use tiberius::{Client, Config, Row};
use tokio::net::TcpStream;
use tokio_util::compat::{Compat, TokioAsyncWriteCompatExt};

use crate::error::ApiError;
use crate::models::CrmStudent;

type SqlClient = Client<Compat<TcpStream>>;

pub mod crm_queries {
    pub const LOGIN: &str = "EXEC dbo.usp_login @P1, @P2";

    pub const LIST_STUDENTS: &str = r#"
        SELECT
            s.Id,
            s.FranchiseID,
            s.FirstName,
            s.LastName,
            s.Grade,
            s.GradePortalURL,
            s.GradePortalUser,
            s.GradePortalPwd,
            f.FranchiesName
        FROM tblStudents s
        LEFT JOIN tblFranchies f ON f.Id = s.FranchiseID
        WHERE (@P1 IS NULL OR s.FranchiseID = @P1)
          AND (@P2 IS NULL OR s.Id = @P2)
        ORDER BY s.LastName, s.FirstName, s.Id
    "#;
}

#[derive(Debug, Clone)]
pub struct CrmLogin {
    pub authenticated: bool,
    pub role: Option<i32>,
    pub franchise_id: Option<i32>,
    pub display_name: Option<String>,
}

fn normalize_field(value: Option<&str>) -> Option<String> {
    value.and_then(|v| {
        let trimmed = v.trim();
        if trimmed.is_empty() {
            None
        } else {
            Some(trimmed.to_string())
        }
    })
}

async fn connect(url: &str) -> Result<SqlClient, ApiError> {
    let config = Config::from_ado_string(url).map_err(|_| ApiError::Unavailable)?;
    let tcp = TcpStream::connect(config.get_addr())
        .await
        .map_err(|_| ApiError::Unavailable)?;
    tcp.set_nodelay(true).map_err(|_| ApiError::Unavailable)?;
    Client::connect(config, tcp.compat_write())
        .await
        .map_err(ApiError::from)
}

fn get_i32(row: &Row, names: &[&str], index_fallback: &[usize]) -> Option<i32> {
    for name in names {
        if let Ok(value) = row.try_get::<i32, _>(*name) {
            if value.is_some() {
                return value;
            }
        }
    }
    for index in index_fallback {
        if let Ok(value) = row.try_get::<i32, _>(*index) {
            if value.is_some() {
                return value;
            }
        }
    }
    None
}

fn get_i64(row: &Row, names: &[&str], index_fallback: &[usize]) -> Option<i64> {
    for name in names {
        if let Ok(value) = row.try_get::<i64, _>(*name) {
            if value.is_some() {
                return value;
            }
        }
        if let Ok(Some(value)) = row.try_get::<i32, _>(*name) {
            return Some(i64::from(value));
        }
    }
    for index in index_fallback {
        if let Ok(value) = row.try_get::<i64, _>(*index) {
            if value.is_some() {
                return value;
            }
        }
        if let Ok(Some(value)) = row.try_get::<i32, _>(*index) {
            return Some(i64::from(value));
        }
    }
    None
}

fn get_string(row: &Row, names: &[&str], index_fallback: &[usize]) -> Option<String> {
    for name in names {
        if let Ok(value) = row.try_get::<&str, _>(*name) {
            if let Some(value) = normalize_field(value) {
                return Some(value);
            }
        }
    }
    for index in index_fallback {
        if let Ok(value) = row.try_get::<&str, _>(*index) {
            if let Some(value) = normalize_field(value) {
                return Some(value);
            }
        }
    }
    None
}

fn row_to_crm_student(row: &Row) -> Result<CrmStudent, ApiError> {
    let crmstudentid = get_i64(row, &["Id", "id", "StudentID"], &[0])
        .ok_or_else(|| ApiError::Safe("CRM student row is missing Id".into()))?;
    let franchiseid = get_i32(row, &["FranchiseID", "franchiseid"], &[1])
        .ok_or_else(|| ApiError::Safe("CRM student row is missing FranchiseID".into()))?;

    Ok(CrmStudent {
        crmstudentid,
        franchiseid,
        firstname: get_string(row, &["FirstName", "firstname"], &[2]).unwrap_or_default(),
        lastname: get_string(row, &["LastName", "lastname"], &[3]).unwrap_or_default(),
        grade: get_i32(row, &["Grade", "grade"], &[4]),
        portal1: get_string(row, &["GradePortalURL", "portal1"], &[5]),
        p1username: get_string(row, &["GradePortalUser", "p1username"], &[6]),
        p1password: get_string(row, &["GradePortalPwd", "p1password"], &[7]),
        franchise_name: get_string(row, &["FranchiesName", "franchise_name"], &[8]),
    })
}

pub async fn login(url: &str, username: &str, password: &str) -> Result<CrmLogin, ApiError> {
    if username.is_empty() || password.is_empty() {
        return Ok(CrmLogin {
            authenticated: false,
            role: None,
            franchise_id: None,
            display_name: None,
        });
    }

    let mut client = connect(url).await?;
    let row = client
        .query(crm_queries::LOGIN, &[&username, &password])
        .await?
        .into_row()
        .await?;

    let Some(row) = row else {
        return Ok(CrmLogin {
            authenticated: false,
            role: None,
            franchise_id: None,
            display_name: None,
        });
    };

    let role = get_i32(&row, &["Role", "role"], &[0]);
    let franchise_id = get_i32(
        &row,
        &["FranchiseID", "franchiseid", "franchiseId", "Id", "id"],
        &[1, 2],
    );
    let display_name = get_string(&row, &["Name", "DisplayName", "displayName"], &[3]);

    let authenticated = role.is_some_and(|value| matches!(value, 2 | 3))
        && franchise_id.is_some_and(|value| value > 0);
    if !authenticated {
        return Ok(CrmLogin {
            authenticated: false,
            role: None,
            franchise_id: None,
            display_name: None,
        });
    }

    Ok(CrmLogin {
        authenticated: true,
        role,
        franchise_id,
        display_name,
    })
}

pub async fn ping(url: &str) -> Result<(), ApiError> {
    let mut client = connect(url).await?;
    client
        .simple_query("SELECT 1")
        .await?
        .into_results()
        .await?;
    Ok(())
}

pub async fn list_students(
    url: &str,
    franchise_id: Option<i32>,
    student_id: Option<i64>,
) -> Result<Vec<CrmStudent>, ApiError> {
    let mut client = connect(url).await?;
    let stream = client
        .query(crm_queries::LIST_STUDENTS, &[&franchise_id, &student_id])
        .await?;
    let rows = stream.into_results().await?;
    let mut students = Vec::new();
    for result_set in rows {
        for row in result_set {
            students.push(row_to_crm_student(&row)?);
        }
    }
    Ok(students)
}
