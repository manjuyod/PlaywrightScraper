use axum::{
    extract::rejection::JsonRejection,
    http::StatusCode,
    response::{IntoResponse, Response},
    Json,
};
use serde::Serialize;
use thiserror::Error;

#[derive(Debug, Error)]
pub enum ApiError {
    #[error("unauthorized")]
    Unauthorized,
    #[error("forbidden")]
    Forbidden,
    #[error("rate limited")]
    RateLimited,
    #[error("not found")]
    NotFound,
    #[error("bad request: {0}")]
    BadRequest(String),
    #[error("conflict: {0}")]
    Conflict(String),
    #[error("service unavailable")]
    Unavailable,
    #[error(transparent)]
    Db(#[from] sqlx::Error),
    #[error(transparent)]
    SqlServer(#[from] tiberius::error::Error),
    #[error("{0}")]
    Safe(String),
    #[error("invalid request body")]
    InvalidBody(#[from] JsonRejection),
}

impl ApiError {
    fn status_code(&self) -> StatusCode {
        match self {
            ApiError::Unauthorized => StatusCode::UNAUTHORIZED,
            ApiError::Forbidden => StatusCode::FORBIDDEN,
            ApiError::RateLimited => StatusCode::TOO_MANY_REQUESTS,
            ApiError::NotFound => StatusCode::NOT_FOUND,
            ApiError::BadRequest(_) => StatusCode::BAD_REQUEST,
            ApiError::Conflict(_) => StatusCode::CONFLICT,
            ApiError::Unavailable => StatusCode::SERVICE_UNAVAILABLE,
            ApiError::Db(_) => StatusCode::SERVICE_UNAVAILABLE,
            ApiError::SqlServer(_) => StatusCode::SERVICE_UNAVAILABLE,
            ApiError::Safe(_) => StatusCode::INTERNAL_SERVER_ERROR,
            ApiError::InvalidBody(_) => StatusCode::BAD_REQUEST,
        }
    }

    fn code(&self) -> &'static str {
        match self {
            ApiError::Unauthorized => "unauthorized",
            ApiError::Forbidden => "forbidden",
            ApiError::RateLimited => "rate_limited",
            ApiError::NotFound => "not_found",
            ApiError::BadRequest(_) => "bad_request",
            ApiError::Conflict(_) => "conflict",
            ApiError::Unavailable => "service_unavailable",
            ApiError::Db(_) => "service_unavailable",
            ApiError::SqlServer(_) => "service_unavailable",
            ApiError::Safe(_) => "internal_error",
            ApiError::InvalidBody(_) => "bad_request",
        }
    }

    fn safe_message(&self) -> String {
        match self {
            ApiError::BadRequest(msg) | ApiError::Conflict(msg) | ApiError::Safe(msg) => {
                msg.clone()
            }
            ApiError::Unauthorized => "Unauthorized".to_string(),
            ApiError::Forbidden => "Forbidden".to_string(),
            ApiError::RateLimited => "Too many requests".to_string(),
            ApiError::NotFound => "Resource not found".to_string(),
            ApiError::Unavailable => "Service unavailable".to_string(),
            ApiError::Db(_) => "Database unavailable".to_string(),
            ApiError::SqlServer(_) => "CRM authentication service unavailable".to_string(),
            ApiError::InvalidBody(_) => "Invalid request body".to_string(),
        }
    }
}

#[derive(Serialize)]
struct ApiErrorPayload {
    code: &'static str,
    message: String,
}

#[derive(Serialize)]
struct ApiErrorBody {
    error: ApiErrorPayload,
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        let payload = ApiErrorBody {
            error: ApiErrorPayload {
                code: self.code(),
                message: self.safe_message(),
            },
        };
        (self.status_code(), Json(payload)).into_response()
    }
}
