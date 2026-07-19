use thiserror::Error;

#[derive(Debug, Error)]
pub enum AppError {
    #[error("request validation failed")]
    Validation(String),
    #[error("configuration is invalid")]
    Config,
    #[error("an active job already exists")]
    Conflict,
    #[error("job lease is unavailable or expired")]
    LeaseExpired,
    #[error("a database dependency is unavailable")]
    Dependency(&'static str),
    #[error("the database boundary failed")]
    Internal,
}

impl AppError {
    pub fn exit_code(&self) -> i32 {
        match self {
            Self::Validation(_) | Self::Config => 2,
            Self::Conflict => 3,
            Self::LeaseExpired => 4,
            Self::Dependency(_) => 5,
            Self::Internal => 1,
        }
    }

    pub fn public_code(&self) -> &'static str {
        match self {
            Self::Validation(_) => "validation_error",
            Self::Config => "configuration_error",
            Self::Conflict => "active_job_conflict",
            Self::LeaseExpired => "lease_expired",
            Self::Dependency(code) => code,
            Self::Internal => "internal_error",
        }
    }
}
