use async_trait::async_trait;
use tiberius::{AuthMethod, Client, Config, Row};
use tokio::net::TcpStream;
use tokio_util::compat::{Compat, TokioAsyncWriteCompatExt};

use crate::config::CrmConfig;
use crate::error::AppError;
use crate::models::CrmStudent;
use crate::service::CrmGateway;

type SqlClient = Client<Compat<TcpStream>>;

pub mod sql {
    pub const PING: &str = "SELECT 1";

    pub const LIST_STUDENTS: &str = r#"
SELECT
    s.Id,
    s.FranchiseID,
    s.FirstName,
    s.LastName,
    s.Grade,
    s.GradePortalURL,
    s.GradePortalUser,
    s.GradePortalPwd
FROM dbo.tblStudents AS s
WHERE (@P1 IS NULL OR s.FranchiseID = @P1)
  AND (@P2 IS NULL OR s.Id = @P2)
ORDER BY s.LastName, s.FirstName, s.Id
"#;
}

pub struct SqlServerCrmGateway {
    config: CrmConfig,
}

impl SqlServerCrmGateway {
    pub fn new(config: CrmConfig) -> Self {
        Self { config }
    }

    async fn connect(&self) -> Result<SqlClient, AppError> {
        let mut config = Config::new();
        config.host(&self.config.host);
        config.port(self.config.port);
        config.database(&self.config.database);
        config.authentication(AuthMethod::sql_server(
            &self.config.username,
            &self.config.password,
        ));
        if self.config.trust_server_certificate {
            config.trust_cert();
        }

        let tcp = TcpStream::connect(config.get_addr())
            .await
            .map_err(|_| AppError::Dependency("crm_unavailable"))?;
        tcp.set_nodelay(true)
            .map_err(|_| AppError::Dependency("crm_unavailable"))?;
        Client::connect(config, tcp.compat_write())
            .await
            .map_err(|_| AppError::Dependency("crm_unavailable"))
    }
}

#[async_trait]
impl CrmGateway for SqlServerCrmGateway {
    async fn ping(&self) -> Result<(), AppError> {
        let mut client = self.connect().await?;
        client
            .simple_query(sql::PING)
            .await
            .map_err(|_| AppError::Dependency("crm_unavailable"))?
            .into_results()
            .await
            .map_err(|_| AppError::Dependency("crm_unavailable"))?;
        Ok(())
    }

    async fn list_students(
        &self,
        franchise_id: Option<i32>,
        student_id: Option<i64>,
    ) -> Result<Vec<CrmStudent>, AppError> {
        let mut client = self.connect().await?;
        let result_sets = client
            .query(sql::LIST_STUDENTS, &[&franchise_id, &student_id])
            .await
            .map_err(|_| AppError::Dependency("crm_unavailable"))?
            .into_results()
            .await
            .map_err(|_| AppError::Dependency("crm_unavailable"))?;
        result_sets.iter().flatten().map(row_to_student).collect()
    }
}

fn row_to_student(row: &Row) -> Result<CrmStudent, AppError> {
    Ok(CrmStudent {
        crmstudentid: required_i64(row, 0)?,
        franchiseid: row
            .try_get::<i32, _>(1)
            .ok()
            .flatten()
            .ok_or(AppError::Dependency("crm_schema_mismatch"))?,
        firstname: string_at(row, 2),
        lastname: string_at(row, 3),
        grade: row.try_get::<i32, _>(4).ok().flatten(),
        portal1: optional_string_at(row, 5),
        p1username: optional_string_at(row, 6),
        p1password: optional_string_at(row, 7),
    })
}

fn required_i64(row: &Row, index: usize) -> Result<i64, AppError> {
    if let Ok(Some(value)) = row.try_get::<i64, _>(index) {
        return Ok(value);
    }
    row.try_get::<i32, _>(index)
        .ok()
        .flatten()
        .map(i64::from)
        .ok_or(AppError::Dependency("crm_schema_mismatch"))
}

fn string_at(row: &Row, index: usize) -> String {
    optional_string_at(row, index).unwrap_or_default()
}

fn optional_string_at(row: &Row, index: usize) -> Option<String> {
    row.try_get::<&str, _>(index)
        .ok()
        .flatten()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
}
