use std::{collections::HashMap, env, net::SocketAddr};

use base64::Engine;

use crate::api_keys::{
    parse_basic_keyring_json, parse_scheduler_keyring_json, validate_cross_role_digest_uniqueness,
    BasicKeyring, SchedulerKeyring,
};
use crate::credentials::AlternateCredentialKeyring;

#[derive(Clone)]
pub struct DashboardHmacVerificationKeys {
    pub active: String,
    pub previous: Option<String>,
}

#[derive(Clone)]
pub struct ApiConfig {
    pub neon_database_url: String,
    pub crm_database_url: String,
    pub worker_api_keyring: BasicKeyring,
    pub scheduler_api_keyring: SchedulerKeyring,
    pub operator_api_keyring: BasicKeyring,
    pub readiness_api_keyring: BasicKeyring,
    pub default_worker_id: String,
    pub worker_lease_seconds: i64,
    pub dashboard_hmac_max_age_seconds: i64,
    pub readiness_timeout_millis: u64,
    pub production_mode: bool,
    pub api_bind_addr: String,
    pub dashboard_hmac_verification_keys: DashboardHmacVerificationKeys,
    pub alternate_credential_keyring: AlternateCredentialKeyring,
    pub allow_plaintext_alternate_credentials: bool,
    pub rust_log: String,
}

const DEFAULT_WORKER_LEASE_SECONDS: i64 = 300;
const MIN_WORKER_LEASE_SECONDS: i64 = 30;
const MAX_WORKER_LEASE_SECONDS: i64 = 3600;
const DEFAULT_DASHBOARD_HMAC_MAX_AGE_SECONDS: i64 = 60;
const MIN_DASHBOARD_HMAC_MAX_AGE_SECONDS: i64 = 10;
const MAX_DASHBOARD_HMAC_MAX_AGE_SECONDS: i64 = 300;
const DEFAULT_READINESS_TIMEOUT_MILLIS: u64 = 2_000;
const MIN_READINESS_TIMEOUT_MILLIS: u64 = 100;
const MAX_READINESS_TIMEOUT_MILLIS: u64 = 5_000;

fn require_env(name: &str) -> Result<String, String> {
    env::var(name).map_err(|_| format!("Missing required environment variable: {name}"))
}

fn optional_env(name: &str, default: &str) -> String {
    env::var(name).unwrap_or_else(|_| default.to_string())
}

fn require_secret_env(name: &str) -> Result<String, String> {
    let value = require_env(name)?;
    if value.is_empty() || value.trim() != value {
        return Err(format!(
            "{name} must be nonempty and must not have surrounding whitespace"
        ));
    }
    Ok(value)
}

fn parse_production_mode(value: Option<&str>) -> Result<bool, String> {
    match value
        .unwrap_or("development")
        .trim()
        .to_ascii_lowercase()
        .as_str()
    {
        "production" => Ok(true),
        "development" | "test" => Ok(false),
        _ => Err("DEPLOYMENT_ENV must be production, development, or test".into()),
    }
}

pub fn validate_api_bind_addr(value: &str, production_mode: bool) -> Result<String, String> {
    let address = value
        .parse::<SocketAddr>()
        .map_err(|_| "API_BIND_ADDR must be an IP socket address".to_string())?;
    if production_mode && !address.ip().is_loopback() {
        return Err("API_BIND_ADDR must use a loopback address in production".into());
    }
    Ok(value.to_string())
}

pub fn parse_dashboard_hmac_verification_keys(
    active: &str,
    previous: Option<&str>,
) -> Result<DashboardHmacVerificationKeys, String> {
    if active.is_empty() || active.trim() != active {
        return Err("DASHBOARD_HMAC_ACTIVE_SECRET must be nonempty and unpadded".into());
    }
    let previous = match previous {
        None => None,
        Some(value) if value.is_empty() || value.trim() != value => {
            return Err("DASHBOARD_HMAC_PREVIOUS_SECRET must be nonempty and unpadded".into())
        }
        Some(value) if value == active => {
            return Err("Dashboard HMAC active and previous secrets must be distinct".into())
        }
        Some(value) => Some(value.to_string()),
    };
    Ok(DashboardHmacVerificationKeys {
        active: active.to_string(),
        previous,
    })
}

pub fn parse_alternate_credential_keyring(
    active_key_id: &str,
    value: &str,
    environment: &str,
) -> Result<AlternateCredentialKeyring, String> {
    let encoded_keys: HashMap<String, String> = serde_json::from_str(value).map_err(|_| {
        "ALTERNATE_CREDENTIAL_KEYS_JSON must be a JSON object of key IDs to base64 keys".to_string()
    })?;
    if encoded_keys.is_empty() {
        return Err("ALTERNATE_CREDENTIAL_KEYS_JSON must include at least one key".into());
    }

    let mut keys = HashMap::with_capacity(encoded_keys.len());
    let mut decoded_keys = std::collections::HashSet::with_capacity(encoded_keys.len());
    for (key_id, encoded_key) in encoded_keys {
        let decoded = base64::engine::general_purpose::STANDARD
            .decode(encoded_key)
            .map_err(|_| "Alternate credential keys must be valid base64".to_string())?;
        let key: [u8; 32] = decoded
            .try_into()
            .map_err(|_| "Alternate credential keys must decode to exactly 32 bytes".to_string())?;
        if !decoded_keys.insert(key) {
            return Err("Alternate credential key material must be unique".into());
        }
        keys.insert(key_id, key);
    }
    AlternateCredentialKeyring::new(active_key_id, keys, environment)
        .map_err(|_| "Invalid alternate credential keyring configuration".to_string())
}

fn parse_bool_flag(value: Option<&str>, environment_name: &str) -> Result<bool, String> {
    match value.map(str::trim).map(str::to_ascii_lowercase).as_deref() {
        None | Some("") | Some("0") | Some("false") | Some("no") => Ok(false),
        Some("1") | Some("true") | Some("yes") => Ok(true),
        Some(_) => Err(format!(
            "{environment_name} must be true/false, yes/no, or 1/0"
        )),
    }
}

pub fn validate_api_keyring_configuration(
    workers: &BasicKeyring,
    schedulers: &SchedulerKeyring,
    operators: &BasicKeyring,
    readiness: &BasicKeyring,
    default_worker_id: &str,
) -> Result<(), String> {
    if default_worker_id.is_empty() || default_worker_id.trim() != default_worker_id {
        return Err("DEFAULT_WORKER_ID must be nonempty and unpadded".into());
    }
    if !workers.contains_key(default_worker_id) {
        return Err("DEFAULT_WORKER_ID must reference a configured worker identity".into());
    }
    if schedulers.values().any(|scheduler| {
        scheduler
            .target_worker_ids
            .iter()
            .any(|worker_id| !workers.contains_key(worker_id))
    }) {
        return Err("Scheduler targets must reference configured worker identities".into());
    }
    validate_cross_role_digest_uniqueness(workers, schedulers, operators, readiness)
}

pub fn parse_worker_lease_seconds(value: Option<&str>) -> Result<i64, String> {
    let seconds = match value {
        Some(value) => value
            .trim()
            .parse::<i64>()
            .map_err(|_| "WORKER_LEASE_SECONDS must be an integer".to_string())?,
        None => DEFAULT_WORKER_LEASE_SECONDS,
    };
    if !(MIN_WORKER_LEASE_SECONDS..=MAX_WORKER_LEASE_SECONDS).contains(&seconds) {
        return Err(format!(
            "WORKER_LEASE_SECONDS must be between {MIN_WORKER_LEASE_SECONDS} and {MAX_WORKER_LEASE_SECONDS}"
        ));
    }
    Ok(seconds)
}

pub fn parse_dashboard_hmac_max_age_seconds(value: Option<&str>) -> Result<i64, String> {
    let seconds = match value {
        Some(value) => value
            .trim()
            .parse::<i64>()
            .map_err(|_| "DASHBOARD_HMAC_MAX_AGE_SECONDS must be an integer".to_string())?,
        None => DEFAULT_DASHBOARD_HMAC_MAX_AGE_SECONDS,
    };
    if !(MIN_DASHBOARD_HMAC_MAX_AGE_SECONDS..=MAX_DASHBOARD_HMAC_MAX_AGE_SECONDS).contains(&seconds)
    {
        return Err(format!(
            "DASHBOARD_HMAC_MAX_AGE_SECONDS must be between {MIN_DASHBOARD_HMAC_MAX_AGE_SECONDS} and {MAX_DASHBOARD_HMAC_MAX_AGE_SECONDS}"
        ));
    }
    Ok(seconds)
}

pub fn parse_readiness_timeout_millis(value: Option<&str>) -> Result<u64, String> {
    let millis = match value {
        Some(value) => value
            .trim()
            .parse::<u64>()
            .map_err(|_| "READINESS_TIMEOUT_MILLIS must be an integer".to_string())?,
        None => DEFAULT_READINESS_TIMEOUT_MILLIS,
    };
    if !(MIN_READINESS_TIMEOUT_MILLIS..=MAX_READINESS_TIMEOUT_MILLIS).contains(&millis) {
        return Err(format!(
            "READINESS_TIMEOUT_MILLIS must be between {MIN_READINESS_TIMEOUT_MILLIS} and {MAX_READINESS_TIMEOUT_MILLIS}"
        ));
    }
    Ok(millis)
}

impl ApiConfig {
    pub fn from_env() -> Result<Self, String> {
        let lease_value = env::var("WORKER_LEASE_SECONDS").ok();
        let dashboard_hmac_max_age_value = env::var("DASHBOARD_HMAC_MAX_AGE_SECONDS").ok();
        let readiness_timeout_value = env::var("READINESS_TIMEOUT_MILLIS").ok();
        let deployment_env = env::var("DEPLOYMENT_ENV").ok();
        let production_mode = parse_production_mode(deployment_env.as_deref())?;
        let deployment_environment = deployment_env
            .as_deref()
            .unwrap_or("development")
            .trim()
            .to_ascii_lowercase();
        let dashboard_hmac_active = require_secret_env("DASHBOARD_HMAC_ACTIVE_SECRET")?;
        let dashboard_hmac_previous = env::var("DASHBOARD_HMAC_PREVIOUS_SECRET").ok();
        let worker_api_keyring =
            parse_basic_keyring_json(&require_env("WORKER_API_KEYRING_JSON")?, "WORKER")?;
        let scheduler_api_keyring =
            parse_scheduler_keyring_json(&require_env("SCHEDULER_API_KEYRING_JSON")?)?;
        let operator_api_keyring =
            parse_basic_keyring_json(&require_env("OPERATOR_API_KEYRING_JSON")?, "OPERATOR")?;
        let readiness_api_keyring =
            parse_basic_keyring_json(&require_env("READINESS_API_KEYRING_JSON")?, "READINESS")?;
        let default_worker_id = require_env("DEFAULT_WORKER_ID")?;
        validate_api_keyring_configuration(
            &worker_api_keyring,
            &scheduler_api_keyring,
            &operator_api_keyring,
            &readiness_api_keyring,
            &default_worker_id,
        )?;
        let alternate_credential_keyring = parse_alternate_credential_keyring(
            &require_env("ALTERNATE_CREDENTIAL_ACTIVE_KEY_ID")?,
            &require_env("ALTERNATE_CREDENTIAL_KEYS_JSON")?,
            &deployment_environment,
        )?;
        let api_bind_addr = validate_api_bind_addr(
            &optional_env("API_BIND_ADDR", "127.0.0.1:3000"),
            production_mode,
        )?;
        Ok(Self {
            neon_database_url: require_env("NEON_DATABASE_URL")?,
            crm_database_url: require_env("CRM_DATABASE_URL")?,
            worker_api_keyring,
            scheduler_api_keyring,
            operator_api_keyring,
            readiness_api_keyring,
            default_worker_id,
            worker_lease_seconds: parse_worker_lease_seconds(lease_value.as_deref())?,
            dashboard_hmac_max_age_seconds: parse_dashboard_hmac_max_age_seconds(
                dashboard_hmac_max_age_value.as_deref(),
            )?,
            readiness_timeout_millis: parse_readiness_timeout_millis(
                readiness_timeout_value.as_deref(),
            )?,
            production_mode,
            api_bind_addr,
            dashboard_hmac_verification_keys: parse_dashboard_hmac_verification_keys(
                &dashboard_hmac_active,
                dashboard_hmac_previous.as_deref(),
            )?,
            alternate_credential_keyring,
            allow_plaintext_alternate_credentials: parse_bool_flag(
                env::var("ALLOW_PLAINTEXT_ALTERNATE_CREDENTIALS")
                    .ok()
                    .as_deref(),
                "ALLOW_PLAINTEXT_ALTERNATE_CREDENTIALS",
            )?,
            rust_log: optional_env("RUST_LOG", "info"),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::{
        parse_alternate_credential_keyring, parse_dashboard_hmac_max_age_seconds,
        parse_worker_lease_seconds, validate_api_keyring_configuration,
    };
    use crate::api_keys::{parse_basic_keyring_json, parse_scheduler_keyring_json};
    use sha2::{Digest, Sha256};

    fn digest(raw: &str) -> String {
        hex::encode(Sha256::digest(raw.as_bytes()))
    }

    fn basic(identity: &str, raw: &str, key_id: &str) -> crate::api_keys::BasicKeyring {
        parse_basic_keyring_json(
            &serde_json::json!({
                identity: {
                    "keys": [{
                        "key_id": key_id,
                        "sha256": digest(raw),
                        "expires_at": "2099-01-01T00:00:00Z"
                    }]
                }
            })
            .to_string(),
            "test",
        )
        .unwrap()
    }

    fn scheduler(targets: &[&str], raw: &str) -> crate::api_keys::SchedulerKeyring {
        parse_scheduler_keyring_json(
            &serde_json::json!({
                "scheduler-a": {
                    "keys": [{
                        "key_id": "primary",
                        "sha256": digest(raw),
                        "expires_at": "2099-01-01T00:00:00Z"
                    }],
                    "franchise_ids": [11],
                    "target_worker_ids": targets,
                    "can_reconcile": false
                }
            })
            .to_string(),
        )
        .unwrap()
    }

    #[test]
    fn default_worker_must_exist() {
        let workers = basic("worker-a", "worker-key", "primary");
        let schedulers = scheduler(&["worker-a"], "scheduler-key");
        let operators = basic("operator-a", "operator-key", "primary");
        let readiness = basic("probe-a", "readiness-key", "primary");

        assert!(validate_api_keyring_configuration(
            &workers,
            &schedulers,
            &operators,
            &readiness,
            "missing-worker",
        )
        .is_err());
    }

    #[test]
    fn scheduler_targets_must_reference_workers() {
        let workers = basic("worker-a", "worker-key", "primary");
        let schedulers = scheduler(&["worker-a", "missing-worker"], "scheduler-key");
        let operators = basic("operator-a", "operator-key", "primary");
        let readiness = basic("probe-a", "readiness-key", "primary");

        assert!(validate_api_keyring_configuration(
            &workers,
            &schedulers,
            &operators,
            &readiness,
            "worker-a",
        )
        .is_err());
    }

    #[test]
    fn digests_must_be_unique_across_roles() {
        let workers = parse_basic_keyring_json(
            &serde_json::json!({
                "worker-a": {"keys": [
                    {"key_id": "old", "sha256": digest("worker-old"), "expires_at": "2099-01-01T00:00:00Z"},
                    {"key_id": "new", "sha256": digest("shared-key"), "expires_at": "2099-02-01T00:00:00Z"}
                ]}
            })
            .to_string(),
            "worker",
        )
        .unwrap();
        let schedulers = scheduler(&["worker-a"], "scheduler-key");
        let operators = basic("operator-a", "shared-key", "primary");
        let readiness = basic("probe-a", "readiness-key", "primary");

        assert!(validate_api_keyring_configuration(
            &workers,
            &schedulers,
            &operators,
            &readiness,
            "worker-a",
        )
        .is_err());
    }

    #[test]
    fn alternate_credential_keyring_requires_an_active_32_byte_base64_key() {
        let encoded =
            base64::Engine::encode(&base64::engine::general_purpose::STANDARD, [7_u8; 32]);
        let keyring = parse_alternate_credential_keyring(
            "key-2026-07",
            &format!(r#"{{"key-2026-07":"{encoded}"}}"#),
            "test",
        )
        .unwrap();
        assert_eq!(keyring.active_key_id(), "key-2026-07");

        for invalid in [
            "{}".to_string(),
            r#"{"other":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}"#.to_string(),
            r#"{"key-2026-07":"not-base64"}"#.to_string(),
        ] {
            assert!(parse_alternate_credential_keyring("key-2026-07", &invalid, "test").is_err());
        }
    }

    #[test]
    fn worker_lease_seconds_is_bounded_with_a_safe_default() {
        assert_eq!(parse_worker_lease_seconds(None).unwrap(), 300);
        assert_eq!(parse_worker_lease_seconds(Some("60")).unwrap(), 60);
        for invalid in ["0", "29", "3601", "not-a-number"] {
            assert!(
                parse_worker_lease_seconds(Some(invalid)).is_err(),
                "{invalid}"
            );
        }
    }

    #[test]
    fn dashboard_hmac_max_age_is_bounded_with_a_safe_default() {
        assert_eq!(parse_dashboard_hmac_max_age_seconds(None).unwrap(), 60);
        assert_eq!(
            parse_dashboard_hmac_max_age_seconds(Some("120")).unwrap(),
            120
        );
        for invalid in ["0", "9", "301", "not-a-number"] {
            assert!(
                parse_dashboard_hmac_max_age_seconds(Some(invalid)).is_err(),
                "{invalid}"
            );
        }
    }

    #[test]
    fn production_api_bind_must_be_loopback() {
        assert_eq!(
            super::validate_api_bind_addr("127.0.0.1:3000", true).unwrap(),
            "127.0.0.1:3000"
        );
        assert!(super::validate_api_bind_addr("0.0.0.0:3000", true).is_err());
        assert!(super::validate_api_bind_addr("[::]:3000", true).is_err());
        assert!(super::validate_api_bind_addr("not-a-socket", true).is_err());
        assert_eq!(
            super::validate_api_bind_addr("0.0.0.0:3000", false).unwrap(),
            "0.0.0.0:3000"
        );
    }

    #[test]
    fn readiness_timeout_is_short_and_bounded() {
        assert_eq!(super::parse_readiness_timeout_millis(None).unwrap(), 2_000);
        assert_eq!(
            super::parse_readiness_timeout_millis(Some("500")).unwrap(),
            500
        );
        for invalid in ["0", "99", "5001", "not-a-number"] {
            assert!(
                super::parse_readiness_timeout_millis(Some(invalid)).is_err(),
                "{invalid}"
            );
        }
    }

    #[test]
    fn dashboard_hmac_verifier_keyring_has_distinct_active_and_previous_keys() {
        let keys =
            super::parse_dashboard_hmac_verification_keys("active-secret", Some("previous-secret"))
                .unwrap();
        assert_eq!(keys.active, "active-secret");
        assert_eq!(keys.previous.as_deref(), Some("previous-secret"));

        for (active, previous) in [
            ("", None),
            (" padded", None),
            ("same", Some("same")),
            ("active", Some("")),
            ("active", Some("previous ")),
        ] {
            assert!(super::parse_dashboard_hmac_verification_keys(active, previous).is_err());
        }
    }
}
