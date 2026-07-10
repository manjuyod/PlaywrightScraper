use std::{env, process::ExitCode};

use api::config::parse_alternate_credential_keyring;
use api::credentials::{
    AlternateCredentialKeyring, AlternateCredentials, EncryptedCredentialEnvelope,
};
use serde::Serialize;
use sqlx::{postgres::PgPoolOptions, FromRow, PgPool};
use zeroize::{Zeroize, ZeroizeOnDrop};

const DEFAULT_LIMIT: i64 = 500;
const MAX_LIMIT: i64 = 10_000;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct Options {
    apply: bool,
    verify: bool,
    rotate_key: bool,
    resume_after: i64,
    limit: i64,
}

impl Default for Options {
    fn default() -> Self {
        Self {
            apply: false,
            verify: false,
            rotate_key: false,
            resume_after: 0,
            limit: DEFAULT_LIMIT,
        }
    }
}

fn parse_options(args: impl IntoIterator<Item = String>) -> Result<Options, &'static str> {
    let mut options = Options::default();
    let mut args = args.into_iter();
    while let Some(argument) = args.next() {
        match argument.as_str() {
            "--apply" => options.apply = true,
            "--verify" => options.verify = true,
            "--rotate-key" => options.rotate_key = true,
            "--resume-after" => {
                options.resume_after = args
                    .next()
                    .ok_or("--resume-after requires a value")?
                    .parse()
                    .map_err(|_| "--resume-after must be an integer")?;
                if options.resume_after < 0 {
                    return Err("--resume-after cannot be negative");
                }
            }
            "--limit" => {
                options.limit = args
                    .next()
                    .ok_or("--limit requires a value")?
                    .parse()
                    .map_err(|_| "--limit must be an integer")?;
                if !(1..=MAX_LIMIT).contains(&options.limit) {
                    return Err("--limit is outside the safe range");
                }
            }
            _ => return Err("unknown argument"),
        }
    }
    Ok(options)
}

#[derive(FromRow, Zeroize, ZeroizeOnDrop)]
struct BackfillRow {
    crmstudentid: i64,
    p2username: Option<String>,
    p2password: Option<String>,
    alternate_credentials_version: Option<i16>,
    alternate_credentials_key_id: Option<String>,
    alternate_credentials_nonce: Option<Vec<u8>>,
    alternate_credentials_ciphertext: Option<Vec<u8>>,
}

impl BackfillRow {
    fn plaintext_credentials(&self) -> Result<Option<AlternateCredentials>, ()> {
        match (self.p2username.as_ref(), self.p2password.as_ref()) {
            (None, None) => Ok(None),
            (Some(username), Some(password))
                if !username.trim().is_empty() && !password.trim().is_empty() =>
            {
                Ok(Some(AlternateCredentials {
                    username: username.clone(),
                    password: password.clone(),
                }))
            }
            _ => Err(()),
        }
    }

    fn envelope(&self) -> Result<Option<EncryptedCredentialEnvelope>, ()> {
        match (
            self.alternate_credentials_version,
            self.alternate_credentials_key_id.as_ref(),
            self.alternate_credentials_nonce.as_ref(),
            self.alternate_credentials_ciphertext.as_ref(),
        ) {
            (None, None, None, None) => Ok(None),
            (Some(version), Some(key_id), Some(nonce), Some(ciphertext)) => {
                Ok(Some(EncryptedCredentialEnvelope {
                    version,
                    key_id: key_id.clone(),
                    nonce: nonce.clone(),
                    ciphertext: ciphertext.clone(),
                }))
            }
            _ => Err(()),
        }
    }
}

#[derive(Default, Serialize)]
struct BackfillSummary {
    mode: &'static str,
    scanned: u64,
    plaintext_complete: u64,
    encrypted: u64,
    verified: u64,
    would_write: u64,
    written: u64,
    errors: u64,
    last_crmstudentid: Option<i64>,
}

async fn load_rows(pool: &PgPool, options: Options) -> Result<Vec<BackfillRow>, sqlx::Error> {
    sqlx::query_as::<_, BackfillRow>(
        r#"
        SELECT
            crmstudentid,
            p2username,
            p2password,
            alternate_credentials_version,
            alternate_credentials_key_id,
            alternate_credentials_nonce,
            alternate_credentials_ciphertext
        FROM students_grades_20262027
        WHERE crmstudentid > $1
          AND (
              p2username IS NOT NULL
              OR p2password IS NOT NULL
              OR alternate_credentials_key_id IS NOT NULL
          )
        ORDER BY crmstudentid
        LIMIT $2
        "#,
    )
    .bind(options.resume_after)
    .bind(options.limit)
    .fetch_all(pool)
    .await
}

async fn write_envelope(
    pool: &PgPool,
    crmstudentid: i64,
    envelope: &EncryptedCredentialEnvelope,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        r#"
        UPDATE students_grades_20262027
        SET alternate_credentials_version = $2,
            alternate_credentials_key_id = $3,
            alternate_credentials_nonce = $4,
            alternate_credentials_ciphertext = $5
        WHERE crmstudentid = $1
        "#,
    )
    .bind(crmstudentid)
    .bind(envelope.version)
    .bind(&envelope.key_id)
    .bind(&envelope.nonce)
    .bind(&envelope.ciphertext)
    .execute(pool)
    .await?;
    Ok(())
}

async fn process_rows(
    pool: &PgPool,
    keyring: &AlternateCredentialKeyring,
    options: Options,
) -> Result<BackfillSummary, sqlx::Error> {
    let rows = load_rows(pool, options).await?;
    let mut summary = BackfillSummary {
        mode: if options.apply { "apply" } else { "dry_run" },
        ..Default::default()
    };

    for row in rows {
        summary.scanned += 1;
        summary.last_crmstudentid = Some(row.crmstudentid);
        let plaintext = match row.plaintext_credentials() {
            Ok(value) => value,
            Err(()) => {
                summary.errors += 1;
                continue;
            }
        };
        if plaintext.is_some() {
            summary.plaintext_complete += 1;
        }
        let envelope = match row.envelope() {
            Ok(value) => value,
            Err(()) => {
                summary.errors += 1;
                continue;
            }
        };
        if envelope.is_some() {
            summary.encrypted += 1;
        }

        let decrypted = match envelope.as_ref() {
            Some(envelope) if options.verify || options.rotate_key => {
                match keyring.decrypt(row.crmstudentid, envelope) {
                    Ok(credentials) => {
                        if options.verify
                            && plaintext
                                .as_ref()
                                .is_some_and(|plain| plain != &credentials)
                        {
                            summary.errors += 1;
                            continue;
                        }
                        if options.verify {
                            summary.verified += 1;
                        }
                        Some(credentials)
                    }
                    Err(_) => {
                        summary.errors += 1;
                        continue;
                    }
                }
            }
            _ => None,
        };

        let source_credentials = if options.rotate_key {
            match envelope.as_ref() {
                Some(existing) if existing.key_id != keyring.active_key_id() => decrypted.as_ref(),
                _ => None,
            }
        } else if envelope.is_none() {
            plaintext.as_ref()
        } else {
            None
        };
        let Some(source_credentials) = source_credentials else {
            continue;
        };
        let new_envelope = match keyring.encrypt(row.crmstudentid, source_credentials) {
            Ok(envelope) => envelope,
            Err(_) => {
                summary.errors += 1;
                continue;
            }
        };
        summary.would_write += 1;
        if options.apply {
            write_envelope(pool, row.crmstudentid, &new_envelope).await?;
            summary.written += 1;
        }
    }
    Ok(summary)
}

fn load_keyring() -> Result<AlternateCredentialKeyring, ()> {
    let active_key_id = env::var("ALTERNATE_CREDENTIAL_ACTIVE_KEY_ID").map_err(|_| ())?;
    let encoded_keys = env::var("ALTERNATE_CREDENTIAL_KEYS_JSON").map_err(|_| ())?;
    let environment = env::var("DEPLOYMENT_ENV").unwrap_or_else(|_| "development".into());
    parse_alternate_credential_keyring(
        &active_key_id,
        &encoded_keys,
        environment.trim().to_ascii_lowercase().as_str(),
    )
    .map_err(|_| ())
}

async fn run(options: Options) -> Result<BackfillSummary, ()> {
    let database_url = env::var("NEON_DATABASE_URL").map_err(|_| ())?;
    let keyring = load_keyring()?;
    let pool = PgPoolOptions::new()
        .max_connections(2)
        .connect(&database_url)
        .await
        .map_err(|_| ())?;
    process_rows(&pool, &keyring, options).await.map_err(|_| ())
}

#[tokio::main]
async fn main() -> ExitCode {
    let options = match parse_options(env::args().skip(1)) {
        Ok(options) => options,
        Err(message) => {
            eprintln!("Alternate credential backfill arguments are invalid: {message}");
            return ExitCode::from(2);
        }
    };
    match run(options).await {
        Ok(summary) => match serde_json::to_string(&summary) {
            Ok(summary) => {
                println!("{summary}");
                ExitCode::SUCCESS
            }
            Err(_) => ExitCode::FAILURE,
        },
        Err(()) => {
            eprintln!("Alternate credential backfill failed.");
            ExitCode::FAILURE
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{parse_options, Options, DEFAULT_LIMIT};

    #[test]
    fn dry_run_is_the_default_and_apply_is_explicit() {
        assert_eq!(
            parse_options(Vec::<String>::new()).unwrap(),
            Options {
                apply: false,
                verify: false,
                rotate_key: false,
                resume_after: 0,
                limit: DEFAULT_LIMIT,
            }
        );
        let apply = parse_options(["--apply".into(), "--verify".into()]).unwrap();
        assert!(apply.apply);
        assert!(apply.verify);
    }

    #[test]
    fn resume_limit_and_rotation_are_bounded() {
        let options = parse_options([
            "--rotate-key".into(),
            "--resume-after".into(),
            "42".into(),
            "--limit".into(),
            "100".into(),
        ])
        .unwrap();
        assert!(options.rotate_key);
        assert_eq!(options.resume_after, 42);
        assert_eq!(options.limit, 100);
        assert!(parse_options(["--limit".into(), "0".into()]).is_err());
    }
}
