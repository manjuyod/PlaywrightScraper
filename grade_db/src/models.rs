use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use uuid::Uuid;

#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum JobKind {
    Grade,
    Agenda,
}

impl JobKind {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Grade => "grade",
            Self::Agenda => "agenda",
        }
    }
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct JobStartRequest {
    pub kind: JobKind,
    #[serde(default)]
    pub franchise_id: Option<i32>,
    #[serde(default)]
    pub student_id: Option<i64>,
}

#[derive(Debug, Clone, Serialize)]
pub struct JobLease {
    pub job_id: Uuid,
    pub lease_token: Uuid,
    pub lease_expires_at: DateTime<Utc>,
    pub kind: JobKind,
    pub franchise_id: Option<i32>,
    pub student_id: Option<i64>,
}

#[derive(Debug, Clone)]
pub struct ActiveJob {
    pub job_id: Uuid,
    pub lease_token: Uuid,
    pub kind: JobKind,
    pub franchise_id: Option<i32>,
    pub student_id: Option<i64>,
}

#[derive(Debug, Clone, Serialize)]
pub struct JobStartResponse {
    #[serde(flatten)]
    pub lease: JobLease,
    pub progress: Progress,
    pub students: Vec<RunnerStudent>,
}

impl JobStartRequest {
    pub fn validate(&self) -> Result<(), &'static str> {
        if self.franchise_id.is_some_and(|value| value <= 0) {
            return Err("franchise_id must be positive");
        }
        if self.student_id.is_some_and(|value| value <= 0) {
            return Err("student_id must be positive");
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, Default, Deserialize, Serialize, PartialEq, Eq)]
pub struct Progress {
    pub total: u32,
    pub attempted: u32,
    pub success: u32,
    pub errors: u32,
}

impl Progress {
    pub fn validate(&self) -> Result<(), &'static str> {
        if self.attempted > self.total {
            return Err("attempted cannot exceed total");
        }
        if self.success + self.errors != self.attempted {
            return Err("success plus errors must equal attempted");
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct JobHeartbeatRequest {
    pub job_id: Uuid,
    pub lease_token: Uuid,
    pub progress: Progress,
}

impl JobHeartbeatRequest {
    pub fn validate(&self) -> Result<(), &'static str> {
        self.progress.validate()
    }
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct JobCompleteRequest {
    pub job_id: Uuid,
    pub lease_token: Uuid,
    pub progress: Progress,
}

impl JobCompleteRequest {
    pub fn validate(&self) -> Result<(), &'static str> {
        self.progress.validate()?;
        if self.progress.attempted != self.progress.total {
            return Err("completed jobs must account for every student");
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct JobFailRequest {
    pub job_id: Uuid,
    pub lease_token: Uuid,
    pub code: String,
}

impl JobFailRequest {
    pub fn validate(&self) -> Result<(), &'static str> {
        if is_safe_code(&self.code) {
            Ok(())
        } else {
            Err("failure code must be a safe identifier")
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CrmStudent {
    pub crmstudentid: i64,
    pub franchiseid: i32,
    pub firstname: String,
    pub lastname: String,
    pub grade: Option<i32>,
    pub portal1: Option<String>,
    pub p1username: Option<String>,
    pub p1password: Option<String>,
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

#[derive(Debug, Clone, Default)]
pub struct StudentGradeState {
    pub crmstudentid: i64,
    pub portal2: Option<String>,
    pub p2username: Option<String>,
    pub p2password: Option<String>,
    pub portal: Option<String>,
    pub track_agenda: bool,
    pub auth_type: Option<String>,
    pub auth_answers: Value,
    pub status: Option<String>,
    pub passwordgood: Option<bool>,
}

#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct RunnerStudent {
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
    pub portal: Option<String>,
    pub track_agenda: bool,
    pub auth_type: Option<String>,
    pub auth_images: Vec<String>,
    pub status: Option<String>,
    pub passwordgood: Option<bool>,
}

pub fn merge_runner_student(crm: &CrmStudent, state: Option<&StudentGradeState>) -> RunnerStudent {
    let auth_images = state
        .and_then(|row| row.auth_answers.as_array())
        .map(|values| {
            values
                .iter()
                .filter_map(Value::as_str)
                .map(str::to_owned)
                .collect()
        })
        .unwrap_or_default();

    RunnerStudent {
        crmstudentid: crm.crmstudentid,
        franchiseid: crm.franchiseid,
        firstname: crm.firstname.clone(),
        lastname: crm.lastname.clone(),
        grade: crm.grade,
        portal1: crm.portal1.clone(),
        p1username: crm.p1username.clone(),
        p1password: crm.p1password.clone(),
        portal2: state.and_then(|row| row.portal2.clone()),
        p2username: state.and_then(|row| row.p2username.clone()),
        p2password: state.and_then(|row| row.p2password.clone()),
        portal: state.and_then(|row| row.portal.clone()),
        track_agenda: state.is_some_and(|row| row.track_agenda),
        auth_type: state.and_then(|row| row.auth_type.clone()),
        auth_images,
        status: state.and_then(|row| row.status.clone()),
        passwordgood: state.and_then(|row| row.passwordgood),
    }
}

pub fn deterministic_result_key(job_id: Uuid, crmstudentid: i64, kind: &str) -> Uuid {
    let name = format!("{job_id}:{crmstudentid}:{kind}");
    Uuid::new_v5(&Uuid::NAMESPACE_URL, name.as_bytes())
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum ResultOutcome {
    GradeSuccess {
        parsed_grades: Value,
    },
    AgendaSuccess {
        weekly_agenda: Value,
    },
    Failure {
        code: String,
        #[serde(default)]
        passwordgood: Option<bool>,
    },
}

impl ResultOutcome {
    pub fn kind(&self) -> &'static str {
        match self {
            Self::GradeSuccess { .. } => "grade",
            Self::AgendaSuccess { .. } => "agenda",
            Self::Failure { .. } => "failure",
        }
    }

    pub fn validate_for_job(&self, job_kind: JobKind) -> Result<(), &'static str> {
        match (self, job_kind) {
            (Self::GradeSuccess { parsed_grades }, JobKind::Grade)
                if parsed_grades.is_object() && !parsed_grades.as_object().unwrap().is_empty() =>
            {
                validate_result_json(parsed_grades)
            }
            (Self::AgendaSuccess { weekly_agenda }, JobKind::Agenda)
                if weekly_agenda.is_object() =>
            {
                validate_result_json(weekly_agenda)
            }
            (Self::Failure { code, .. }, _) if is_safe_code(code) => Ok(()),
            (Self::GradeSuccess { .. }, JobKind::Agenda)
            | (Self::AgendaSuccess { .. }, JobKind::Grade) => {
                Err("result kind does not match job kind")
            }
            _ => Err("result payload is invalid"),
        }
    }
}

const MAX_RESULT_DEPTH: usize = 8;
const MAX_RESULT_NODES: usize = 1_000;
const MAX_RESULT_STRING_BYTES: usize = 4_096;
const SENSITIVE_RESULT_KEYS: &[&str] = &[
    "password",
    "p1password",
    "p2password",
    "username",
    "p1username",
    "p2username",
    "secret",
    "token",
    "accesstoken",
    "refreshtoken",
    "authorization",
    "authheader",
    "authanswer",
    "authanswers",
    "authimages",
    "apikey",
    "privatekey",
    "credential",
    "credentials",
    "session",
    "sessionid",
    "cookie",
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

fn validate_result_json(value: &Value) -> Result<(), &'static str> {
    fn visit(value: &Value, depth: usize, nodes: &mut usize) -> Result<(), &'static str> {
        if depth > MAX_RESULT_DEPTH {
            return Err("result payload is too deeply nested");
        }
        *nodes += 1;
        if *nodes > MAX_RESULT_NODES {
            return Err("result payload is too large");
        }
        match value {
            Value::String(text) if text.len() > MAX_RESULT_STRING_BYTES => {
                Err("result payload string is too large")
            }
            Value::Object(object) => {
                for (key, nested) in object {
                    let normalized = normalized_result_key(key);
                    if SENSITIVE_RESULT_KEYS.contains(&normalized.as_str()) {
                        return Err("result payload contains a sensitive field");
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

fn is_safe_code(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 64
        && value.bytes().all(|byte| {
            byte.is_ascii_lowercase() || byte.is_ascii_digit() || matches!(byte, b'_' | b'-')
        })
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct ResultPostRequest {
    pub job_id: Uuid,
    pub lease_token: Uuid,
    pub crmstudentid: i64,
    pub outcome: ResultOutcome,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct ResultPostResponse {
    pub applied: bool,
    pub duplicate: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub rejection_code: Option<String>,
}

impl ResultPostRequest {
    pub fn audit_payload(&self, applied: bool, rejection_code: Option<&str>) -> Value {
        if !applied {
            return json!({
                "status": "rejected",
                "rejection_code": rejection_code.unwrap_or("not_applied"),
            });
        }

        match &self.outcome {
            ResultOutcome::GradeSuccess { parsed_grades } => json!({
                "status": "synced",
                "kind": "grade",
                "parsed_grades": parsed_grades,
            }),
            ResultOutcome::AgendaSuccess { weekly_agenda } => json!({
                "status": "synced",
                "kind": "agenda",
                "weekly_agenda": weekly_agenda,
            }),
            ResultOutcome::Failure { code, passwordgood } => json!({
                "status": "error",
                "kind": "failure",
                "code": code,
                "passwordgood": passwordgood,
            }),
        }
    }
}
