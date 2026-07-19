use std::io::{self, Read};
use std::sync::Arc;

use clap::Parser;
use grade_db::cli::{Cli, Command, JobCommand, ResultCommand};
use grade_db::config::AppConfig;
use grade_db::crm::SqlServerCrmGateway;
use grade_db::error::AppError;
use grade_db::models::{
    JobCompleteRequest, JobFailRequest, JobHeartbeatRequest, JobStartRequest, ResultPostRequest,
};
use grade_db::neon::PostgresNeonGateway;
use grade_db::service::{BoundaryService, CrmGateway, NeonGateway};
use serde::de::DeserializeOwned;
use serde_json::{json, Value};

#[tokio::main]
async fn main() {
    let cli = Cli::parse();
    match execute(cli).await {
        Ok(response) => write_stdout(&response),
        Err(error) => {
            write_stdout(&json!({"ok": false, "error": error.public_code()}));
            eprintln!("grade-db: {}", error.public_code());
            std::process::exit(error.exit_code());
        }
    }
}

async fn execute(cli: Cli) -> Result<Value, AppError> {
    match cli.command {
        Command::Job {
            command: JobCommand::Start,
        } => {
            let request: JobStartRequest = read_stdin_json()?;
            let (config, crm, neon) = runtime().await?;
            let service =
                BoundaryService::new(crm, Arc::new(neon), config.runner_id, config.lease_seconds);
            serde_json::to_value(service.start_job(request).await?).map_err(|_| AppError::Internal)
        }
        Command::Result {
            command: ResultCommand::Post,
        } => {
            let request: ResultPostRequest = read_stdin_json()?;
            let (config, crm, neon) = runtime().await?;
            let service =
                BoundaryService::new(crm, Arc::new(neon), config.runner_id, config.lease_seconds);
            serde_json::to_value(service.post_result(request).await?)
                .map_err(|_| AppError::Internal)
        }
        Command::Job {
            command: JobCommand::Heartbeat,
        } => {
            let request: JobHeartbeatRequest = read_stdin_json()?;
            request
                .validate()
                .map_err(|message| AppError::Validation(message.into()))?;
            let (_, _, neon) = runtime().await?;
            let progress =
                serde_json::to_value(request.progress).map_err(|_| AppError::Internal)?;
            neon.heartbeat(request.job_id, request.lease_token, &progress)
                .await?;
            Ok(json!({"ok": true}))
        }
        Command::Job {
            command: JobCommand::Complete,
        } => {
            let request: JobCompleteRequest = read_stdin_json()?;
            request
                .validate()
                .map_err(|message| AppError::Validation(message.into()))?;
            let (_, _, neon) = runtime().await?;
            let progress =
                serde_json::to_value(request.progress).map_err(|_| AppError::Internal)?;
            neon.complete(request.job_id, request.lease_token, &progress)
                .await?;
            Ok(json!({"ok": true}))
        }
        Command::Job {
            command: JobCommand::Fail,
        } => {
            let request: JobFailRequest = read_stdin_json()?;
            request
                .validate()
                .map_err(|message| AppError::Validation(message.into()))?;
            let (_, _, neon) = runtime().await?;
            neon.fail(request.job_id, request.lease_token, &request.code)
                .await?;
            Ok(json!({"ok": true}))
        }
        Command::Doctor => doctor().await,
    }
}

async fn runtime() -> Result<(AppConfig, Arc<dyn CrmGateway>, PostgresNeonGateway), AppError> {
    let config = AppConfig::from_env().map_err(|_| AppError::Config)?;
    let crm: Arc<dyn CrmGateway> = Arc::new(SqlServerCrmGateway::new(config.crm.clone()));
    let neon = PostgresNeonGateway::connect(
        &config.neon_database_url,
        config.runner_id.clone(),
        config.lease_seconds,
    )
    .await?;
    Ok((config, crm, neon))
}

async fn doctor() -> Result<Value, AppError> {
    let config = AppConfig::from_env().map_err(|_| AppError::Config)?;
    let crm = SqlServerCrmGateway::new(config.crm);
    let crm_ok = crm.ping().await.is_ok();
    let (neon_ok, schema_ok) = match PostgresNeonGateway::connect(
        &config.neon_database_url,
        config.runner_id,
        config.lease_seconds,
    )
    .await
    {
        Ok(neon) => (
            neon.ping().await.is_ok(),
            neon.schema_ready().await.unwrap_or(false),
        ),
        Err(_) => (false, false),
    };
    Ok(json!({
        "ok": crm_ok && neon_ok && schema_ok,
        "checks": {
            "configuration": true,
            "crm_read_only": crm_ok,
            "neon_read_only": neon_ok,
            "schema": schema_ok,
        }
    }))
}

fn read_stdin_json<T: DeserializeOwned>() -> Result<T, AppError> {
    let mut input = String::new();
    io::stdin()
        .take(16 * 1024 * 1024)
        .read_to_string(&mut input)
        .map_err(|_| AppError::Validation("unable to read stdin".into()))?;
    if input.trim().is_empty() {
        return Err(AppError::Validation("stdin JSON is required".into()));
    }
    serde_json::from_str(&input).map_err(|_| AppError::Validation("invalid stdin JSON".into()))
}

fn write_stdout(value: &Value) {
    match serde_json::to_string(value) {
        Ok(encoded) => println!("{encoded}"),
        Err(_) => println!("{{\"ok\":false,\"error\":\"internal_error\"}}"),
    }
}
