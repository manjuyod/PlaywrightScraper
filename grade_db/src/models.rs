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
        if is_process_failure_code(&self.code) {
            Ok(())
        } else {
            Err("failure code is not an allowed process failure")
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
    pub portal2: Option<String>,
    pub p2username: Option<String>,
    pub p2password: Option<String>,
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
    pub portal: Option<String>,
    pub track_agenda: bool,
    pub weeklydata: Value,
    pub auth_type: Option<String>,
    pub auth_answers: Value,
    pub grade_status: Option<String>,
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
    pub known_course_titles: Vec<String>,
    pub auth_type: Option<String>,
    pub auth_images: Vec<String>,
    pub grade_status: Option<String>,
    pub passwordgood: Option<bool>,
}

fn latest_course_titles(weeklydata: &Value) -> Vec<String> {
    let Some(weeks) = weeklydata.as_object() else {
        return Vec::new();
    };
    let Some((_week, courses)) = weeks
        .iter()
        .filter(|(_week, courses)| courses.as_object().is_some_and(|value| !value.is_empty()))
        .max_by_key(|(week, _courses)| *week)
    else {
        return Vec::new();
    };

    let mut titles = courses
        .as_object()
        .into_iter()
        .flat_map(|courses| courses.keys())
        .filter(|title| !title.trim().is_empty())
        .cloned()
        .collect::<Vec<_>>();
    titles.sort_by_cached_key(|title| (title.to_lowercase(), title.clone()));
    titles
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
        portal2: crm.portal2.clone(),
        p2username: crm.p2username.clone(),
        p2password: crm.p2password.clone(),
        portal: state.and_then(|row| row.portal.clone()),
        track_agenda: state.is_some_and(|row| row.track_agenda),
        known_course_titles: state
            .map(|row| latest_course_titles(&row.weeklydata))
            .unwrap_or_default(),
        auth_type: state.and_then(|row| row.auth_type.clone()),
        auth_images,
        grade_status: state.and_then(|row| row.grade_status.clone()),
        passwordgood: state.and_then(|row| row.passwordgood),
    }
}

pub fn deterministic_result_key(job_id: Uuid, crmstudentid: i64, kind: &str) -> Uuid {
    let name = format!("{job_id}:{crmstudentid}:{kind}");
    Uuid::new_v5(&Uuid::NAMESPACE_URL, name.as_bytes())
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ResultChannel {
    Grade,
    PrimaryAgenda,
    SecondaryAgenda,
}

impl ResultChannel {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Grade => "grade",
            Self::PrimaryAgenda => "primary_agenda",
            Self::SecondaryAgenda => "secondary_agenda",
        }
    }

    fn supports_job(&self, job_kind: JobKind) -> bool {
        matches!(
            (self, job_kind),
            (Self::Grade, JobKind::Grade)
                | (Self::PrimaryAgenda | Self::SecondaryAgenda, JobKind::Agenda)
        )
    }
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum ResultOutcome {
    GradeSuccess {
        parsed_grades: Value,
    },
    PrimaryAgendaSuccess {
        agenda: Value,
    },
    SecondaryAgendaSuccess {
        agenda: Value,
    },
    Failure {
        channel: ResultChannel,
        code: String,
        #[serde(default)]
        passwordgood: Option<bool>,
    },
}

impl ResultOutcome {
    pub fn channel(&self) -> ResultChannel {
        match self {
            Self::GradeSuccess { .. } => ResultChannel::Grade,
            Self::PrimaryAgendaSuccess { .. } => ResultChannel::PrimaryAgenda,
            Self::SecondaryAgendaSuccess { .. } => ResultChannel::SecondaryAgenda,
            Self::Failure { channel, .. } => channel.clone(),
        }
    }

    pub fn validate_for_job(&self, job_kind: JobKind) -> Result<(), &'static str> {
        match (self, job_kind) {
            (Self::GradeSuccess { parsed_grades }, JobKind::Grade)
                if parsed_grades.is_object() && !parsed_grades.as_object().unwrap().is_empty() =>
            {
                validate_result_json(parsed_grades)
            }
            (Self::PrimaryAgendaSuccess { agenda }, JobKind::Agenda)
            | (Self::SecondaryAgendaSuccess { agenda }, JobKind::Agenda)
                if agenda.is_object() =>
            {
                validate_result_json(agenda)
            }
            (
                Self::Failure {
                    channel,
                    code,
                    passwordgood,
                },
                job_kind,
            ) if channel.supports_job(job_kind)
                && is_channel_failure_code(channel, code)
                && (matches!(channel, ResultChannel::Grade) || passwordgood.is_none()) =>
            {
                Ok(())
            }
            (Self::GradeSuccess { .. }, JobKind::Agenda)
            | (
                Self::PrimaryAgendaSuccess { .. } | Self::SecondaryAgendaSuccess { .. },
                JobKind::Grade,
            ) => Err("result kind does not match job kind"),
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

fn is_channel_failure_code(channel: &ResultChannel, code: &str) -> bool {
    match channel {
        ResultChannel::Grade => matches!(code, "bad_login" | "no_grades" | "scrape_failed"),
        ResultChannel::PrimaryAgenda | ResultChannel::SecondaryAgenda => matches!(
            code,
            "bad_login" | "configuration_missing" | "scrape_failed" | "unsupported_portal"
        ),
    }
}

fn is_process_failure_code(code: &str) -> bool {
    matches!(
        code,
        "agenda_runner_failed"
            | "lease_expired"
            | "lease_renewal_failed"
            | "neon_unavailable"
            | "result_post_failed"
            | "runner_failed"
    )
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
            ResultOutcome::PrimaryAgendaSuccess { agenda } => json!({
                "status": "synced",
                "kind": "primary_agenda",
                "agenda": agenda,
            }),
            ResultOutcome::SecondaryAgendaSuccess { agenda } => json!({
                "status": "synced",
                "kind": "secondary_agenda",
                "agenda": agenda,
            }),
            ResultOutcome::Failure {
                channel,
                code,
                passwordgood,
            } => json!({
                "status": "error",
                "kind": channel.as_str(),
                "code": code,
                "passwordgood": passwordgood,
            }),
        }
    }
}
