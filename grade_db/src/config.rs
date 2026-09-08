use std::env;

use anyhow::{anyhow, bail, Context, Result};

#[derive(Debug, Clone)]
pub struct CrmConfig {
    pub host: String,
    pub port: u16,
    pub database: String,
    pub username: String,
    pub password: String,
    pub trust_server_certificate: bool,
}

#[derive(Debug, Clone)]
pub struct AppConfig {
    pub neon_database_url: String,
    pub crm: CrmConfig,
    pub runner_id: String,
    pub lease_seconds: i64,
}

pub fn normalize_postgres_url(value: &str) -> String {
    if let Some(rest) = value.strip_prefix("postgresql+psycopg://") {
        format!("postgresql://{rest}")
    } else if let Some(rest) = value.strip_prefix("postgres://") {
        format!("postgresql://{rest}")
    } else {
        value.to_owned()
    }
}

pub fn parse_bool(value: &str) -> Result<bool> {
    match value.trim().to_ascii_lowercase().as_str() {
        "1" | "true" | "yes" => Ok(true),
        "0" | "false" | "no" | "" => Ok(false),
        _ => bail!("invalid boolean flag"),
    }
}

pub fn parse_crm_address(value: &str) -> Result<(String, u16)> {
    let value = value.trim().strip_prefix("tcp:").unwrap_or(value.trim());
    if value.is_empty() {
        bail!("CRM server address is required");
    }
    let (host, port) = match value.rsplit_once(',') {
        Some((host, port)) => (
            host.trim(),
            port.trim().parse::<u16>().context("invalid CRM port")?,
        ),
        None => (value, 1433),
    };
    if host.is_empty() {
        bail!("CRM server host is required");
    }
    Ok((host.to_owned(), port))
}

fn required_env(name: &str) -> Result<String> {
    let value = env::var(name).unwrap_or_default();
    let trimmed = value.trim();
    if trimmed.is_empty() {
        bail!("{name} is required");
    }
    Ok(trimmed.to_owned())
}

fn neon_url_from_env() -> Result<String> {
    if let Ok(value) = env::var("GRADES_NEON_URL") {
        if !value.trim().is_empty() {
            return Ok(normalize_postgres_url(value.trim()));
        }
    }
    let host = required_env("GRADES_NEON_HOST")?;
    let database = env::var("GRADES_NEON_DB")
        .or_else(|_| env::var("GRADES_NEON_DATABASE"))
        .map_err(|_| anyhow!("GRADES_NEON_DB is required"))?;
    let user = required_env("GRADES_NEON_USER")?;
    let password = required_env("GRADES_NEON_PASSWORD")?;
    let port = env::var("GRADES_NEON_PORT")
        .ok()
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| "5432".into());
    let mut url = url::Url::parse("postgresql://localhost")?;
    url.set_host(Some(&host))?;
    url.set_port(Some(port.parse()?))
        .map_err(|_| anyhow!("invalid Neon port"))?;
    url.set_username(&user)
        .map_err(|_| anyhow!("invalid Neon username"))?;
    url.set_password(Some(&password))
        .map_err(|_| anyhow!("invalid Neon password"))?;
    url.set_path(database.trim());
    url.set_query(Some("sslmode=require"));
    Ok(url.to_string())
}

impl AppConfig {
    pub fn from_env() -> Result<Self> {
        let _ = dotenvy::dotenv();
        let database = env::var("CRMSrvDb")
            .ok()
            .filter(|value| !value.trim().is_empty())
            .or_else(|| env::var("CRMSrvDbQA").ok())
            .ok_or_else(|| anyhow!("CRMSrvDb or CRMSrvDbQA is required"))?;
        let (host, port) = parse_crm_address(&required_env("CRMSrvAddress")?)?;
        let lease_seconds = env::var("GRADE_JOB_LEASE_SECONDS")
            .ok()
            .filter(|value| !value.trim().is_empty())
            .map(|value| value.parse::<i64>())
            .transpose()
            .context("GRADE_JOB_LEASE_SECONDS must be an integer")?
            .unwrap_or(600);
        if !(120..=86_400).contains(&lease_seconds) {
            bail!("GRADE_JOB_LEASE_SECONDS must be between 120 and 86400");
        }
        let runner_id = env::var("GRADE_RUNNER_ID")
            .ok()
            .filter(|value| !value.trim().is_empty())
            .unwrap_or_else(|| {
                hostname::get()
                    .unwrap_or_default()
                    .to_string_lossy()
                    .into_owned()
            });
        if runner_id.trim().is_empty() || runner_id.len() > 128 {
            bail!("GRADE_RUNNER_ID must be between 1 and 128 characters");
        }

        Ok(Self {
            neon_database_url: neon_url_from_env()?,
            crm: CrmConfig {
                host,
                port,
                database: database.trim().to_owned(),
                username: required_env("CRMSrvUs")?,
                password: required_env("CRMSrvPs")?,
                trust_server_certificate: parse_bool(
                    &env::var("CRM_TRUST_SERVER_CERTIFICATE").unwrap_or_default(),
                )?,
            },
            runner_id,
            lease_seconds,
        })
    }
}
