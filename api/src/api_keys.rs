use std::collections::{HashMap, HashSet};
use std::sync::Arc;

use chrono::{DateTime, Utc};
use serde::Deserialize;
use sha2::{Digest, Sha256};
use subtle::ConstantTimeEq;

#[derive(Clone)]
pub struct ApiKeyRecord {
    pub key_id: String,
    digest: [u8; 32],
    pub expires_at: DateTime<Utc>,
}

#[derive(Clone)]
pub struct BasicKeyIdentity {
    pub keys: Vec<ApiKeyRecord>,
}

#[derive(Clone)]
pub struct SchedulerKeyIdentity {
    pub keys: Vec<ApiKeyRecord>,
    pub franchise_ids: HashSet<i32>,
    pub target_worker_ids: HashSet<String>,
    pub can_reconcile: bool,
}

pub type BasicKeyring = HashMap<String, BasicKeyIdentity>;
pub type SchedulerKeyring = HashMap<String, SchedulerKeyIdentity>;

pub struct AuthenticatedKey {
    pub identity: String,
    pub key_id: String,
}

pub struct AuthenticatedSchedulerKey {
    pub identity: String,
    pub key_id: String,
    pub franchise_ids: Arc<HashSet<i32>>,
    pub target_worker_ids: Arc<HashSet<String>>,
    pub can_reconcile: bool,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawApiKeyRecord {
    key_id: String,
    sha256: String,
    expires_at: DateTime<Utc>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawBasicIdentity {
    keys: Vec<RawApiKeyRecord>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawSchedulerIdentity {
    keys: Vec<RawApiKeyRecord>,
    franchise_ids: Vec<i32>,
    target_worker_ids: Vec<String>,
    can_reconcile: bool,
}

fn parse_hex_digest(value: &str) -> Result<[u8; 32], String> {
    if value.trim() != value {
        return Err("SHA-256 digests cannot include leading or trailing whitespace".into());
    }
    if value.chars().count() != 64 {
        return Err("SHA-256 digest values must be exactly 64 hex characters".into());
    }
    if value != value.to_ascii_lowercase() {
        return Err("SHA-256 digests must be lowercase hex".into());
    }

    let raw = hex::decode(value).map_err(|_| "SHA-256 digest must be valid hex".to_string())?;
    raw.try_into()
        .map_err(|_| "SHA-256 digest must decode to exactly 32 bytes".into())
}

fn parse_identity_name(role: &str, identity: &str) -> Result<(), String> {
    if identity.trim() != identity {
        return Err(format!(
            "{role} keyring identities must not have surrounding whitespace"
        ));
    }
    if identity.is_empty() {
        Err(format!(
            "{role} keyring must not contain empty identity names"
        ))
    } else {
        Ok(())
    }
}

fn parse_identity_keys(
    role: &str,
    identity: &str,
    entries: &[RawApiKeyRecord],
    seen_digests: &mut HashSet<[u8; 32]>,
) -> Result<Vec<ApiKeyRecord>, String> {
    if entries.is_empty() {
        return Err(format!(
            "{role} identity {identity} must include at least one key"
        ));
    }

    let mut records = Vec::with_capacity(entries.len());
    let mut seen_key_ids = HashSet::with_capacity(entries.len());
    let mut seen_local_digests = HashSet::with_capacity(entries.len());

    for entry in entries {
        let key_id = entry.key_id.trim();
        if key_id.is_empty() {
            return Err(format!("{role} identity {identity} key_id cannot be empty"));
        }
        if key_id != entry.key_id {
            return Err(format!(
                "{role} identity {identity} key_id cannot be padded"
            ));
        }
        if !seen_key_ids.insert(key_id.to_string()) {
            return Err(format!(
                "{role} identity {identity} cannot assign duplicate key IDs"
            ));
        }

        let digest = parse_hex_digest(&entry.sha256)?;
        if !seen_local_digests.insert(digest) {
            return Err(format!(
                "{role} identity {identity} cannot include duplicate key digests"
            ));
        }
        if !seen_digests.insert(digest) {
            return Err(format!(
                "{role} identity {identity} reuses a key digest already assigned to another identity"
            ));
        }

        records.push(ApiKeyRecord {
            key_id: key_id.to_string(),
            digest,
            expires_at: entry.expires_at,
        });
    }

    Ok(records)
}

pub fn parse_basic_keyring_json(value: &str, role: &str) -> Result<BasicKeyring, String> {
    let raw: HashMap<String, RawBasicIdentity> = serde_json::from_str(value).map_err(|_| {
        format!("{role}_API_KEYRING_JSON must be a JSON object of identities to keyrings")
    })?;
    if raw.is_empty() {
        return Err(format!(
            "{role}_API_KEYRING_JSON must include at least one identity"
        ));
    }

    let mut keyring = BasicKeyring::with_capacity(raw.len());
    let mut seen_worker_ids = HashSet::with_capacity(raw.len());
    let mut seen_digests = HashSet::with_capacity(raw.len());

    for (identity, record) in raw {
        parse_identity_name(role, &identity)?;
        let lowered = identity.trim().to_string();
        if !seen_worker_ids.insert(lowered.to_ascii_lowercase()) {
            return Err(format!(
                "{role} keyring cannot contain duplicate identities"
            ));
        }

        keyring.insert(
            lowered,
            BasicKeyIdentity {
                keys: parse_identity_keys(role, &identity, &record.keys, &mut seen_digests)?,
            },
        );
    }

    Ok(keyring)
}

pub fn parse_scheduler_keyring_json(value: &str) -> Result<SchedulerKeyring, String> {
    let raw: HashMap<String, RawSchedulerIdentity> = serde_json::from_str(value).map_err(|_| {
        "SCHEDULER_API_KEYRING_JSON must be a JSON object of identities to keyrings".to_string()
    })?;
    if raw.is_empty() {
        return Err("SCHEDULER_API_KEYRING_JSON must include at least one identity".into());
    }

    let mut keyring = SchedulerKeyring::with_capacity(raw.len());
    let mut seen_scheduler_ids = HashSet::with_capacity(raw.len());
    let mut seen_digests = HashSet::with_capacity(raw.len());

    for (identity, record) in raw {
        parse_identity_name("scheduler", &identity)?;
        let normalized = identity.trim().to_string();
        if !seen_scheduler_ids.insert(normalized.to_ascii_lowercase()) {
            return Err("SCHEDULER_API_KEYRING_JSON cannot contain duplicate identities".into());
        }
        if record.franchise_ids.is_empty() {
            return Err(format!(
                "Scheduler identity {identity} must list at least one franchise_id"
            ));
        }
        if record.franchise_ids.iter().any(|value| *value <= 0) {
            return Err(format!(
                "Scheduler identity {identity} franchise_ids must be positive"
            ));
        }
        if record.target_worker_ids.is_empty() {
            return Err(format!(
                "Scheduler identity {identity} must list at least one target_worker_id"
            ));
        }
        if record
            .target_worker_ids
            .iter()
            .any(|value| value.is_empty() || value.trim() != value)
        {
            return Err(format!(
                "Scheduler identity {identity} target_worker_ids must be nonempty and unpadded"
            ));
        }

        keyring.insert(
            normalized,
            SchedulerKeyIdentity {
                keys: parse_identity_keys("scheduler", &identity, &record.keys, &mut seen_digests)?,
                franchise_ids: record.franchise_ids.into_iter().collect(),
                target_worker_ids: record
                    .target_worker_ids
                    .into_iter()
                    .map(|value| value.trim().to_string())
                    .collect(),
                can_reconcile: record.can_reconcile,
            },
        );
    }

    Ok(keyring)
}

pub fn identify_basic_key(
    keyring: &BasicKeyring,
    raw_key: &str,
    now: DateTime<Utc>,
) -> Option<AuthenticatedKey> {
    let digest: [u8; 32] = Sha256::digest(raw_key.as_bytes()).into();
    let mut matches = Vec::new();

    for (identity, identity_keys) in keyring {
        for key in &identity_keys.keys {
            if now >= key.expires_at {
                continue;
            }
            if key.digest.ct_eq(&digest).unwrap_u8() == 1 {
                matches.push((identity, key));
            }
        }
    }

    if matches.len() != 1 {
        return None;
    }

    Some(AuthenticatedKey {
        identity: matches[0].0.clone(),
        key_id: matches[0].1.key_id.clone(),
    })
}

pub fn identify_scheduler_key(
    keyring: &SchedulerKeyring,
    raw_key: &str,
    now: DateTime<Utc>,
) -> Option<AuthenticatedSchedulerKey> {
    let digest: [u8; 32] = Sha256::digest(raw_key.as_bytes()).into();
    let mut matches = Vec::new();

    for (identity, identity_keys) in keyring {
        for key in &identity_keys.keys {
            if now >= key.expires_at {
                continue;
            }
            if key.digest.ct_eq(&digest).unwrap_u8() == 1 {
                matches.push((identity, key, identity_keys));
            }
        }
    }

    if matches.len() != 1 {
        return None;
    }

    let (identity, key, identity_keys) = matches
        .into_iter()
        .next()
        .expect("single matched scheduler key exists");
    Some(AuthenticatedSchedulerKey {
        identity: identity.clone(),
        key_id: key.key_id.clone(),
        franchise_ids: Arc::new(identity_keys.franchise_ids.clone()),
        target_worker_ids: Arc::new(identity_keys.target_worker_ids.clone()),
        can_reconcile: identity_keys.can_reconcile,
    })
}

pub fn validate_cross_role_digest_uniqueness(
    worker: &BasicKeyring,
    scheduler: &SchedulerKeyring,
    operator: &BasicKeyring,
    readiness: &BasicKeyring,
) -> Result<(), String> {
    let mut seen = HashSet::<[u8; 32]>::new();
    for identity in worker.values() {
        for key in &identity.keys {
            if !seen.insert(key.digest) {
                return Err("API key digests must be unique across roles".into());
            }
        }
    }
    for identity in scheduler.values() {
        for key in &identity.keys {
            if !seen.insert(key.digest) {
                return Err("API key digests must be unique across roles".into());
            }
        }
    }
    for keyring in [operator, readiness] {
        for identity in keyring.values() {
            for key in &identity.keys {
                if !seen.insert(key.digest) {
                    return Err("API key digests must be unique across roles".into());
                }
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use sha2::{Digest, Sha256};

    fn now(value: &str) -> DateTime<Utc> {
        value.parse().unwrap()
    }

    #[test]
    fn parses_two_rotation_keys_for_one_identity() {
        let first = Sha256::digest("old-worker-key").to_vec();
        let second = Sha256::digest("new-worker-key").to_vec();
        let json = serde_json::json!({
            "dev-alice": {
                "keys": [
                    {
                        "key_id": "old",
                        "sha256": hex::encode(first),
                        "expires_at": "2099-01-01T00:00:00Z"
                    },
                    {
                        "key_id": "new",
                        "sha256": hex::encode(second),
                        "expires_at": "2099-02-01T00:00:00Z"
                    }
                ]
            }
        });
        let keyring = parse_basic_keyring_json(&json.to_string(), "worker").unwrap();
        let identity = keyring.get("dev-alice").expect("identity exists");
        assert_eq!(identity.keys.len(), 2);
    }

    #[test]
    fn identifies_both_overlap_keys_as_the_same_identity() {
        let old_raw = "old-worker-key";
        let new_raw = "new-worker-key";
        let json = serde_json::json!({
            "dev-alice-laptop": {
                "keys": [
                    {"key_id": "old", "sha256": hex::encode(Sha256::digest(old_raw.as_bytes())), "expires_at": "2099-01-01T00:00:00Z"},
                    {"key_id": "new", "sha256": hex::encode(Sha256::digest(new_raw.as_bytes())), "expires_at": "2099-02-01T00:00:00Z"}
                ]
            }
        });
        let keyring = parse_basic_keyring_json(&json.to_string(), "worker").unwrap();
        let now = "2098-01-01T00:00:00Z".parse().unwrap();
        let old = identify_basic_key(&keyring, old_raw, now).unwrap();
        let new = identify_basic_key(&keyring, new_raw, now).unwrap();
        assert_eq!(old.identity, "dev-alice-laptop");
        assert_eq!(new.identity, "dev-alice-laptop");
        assert_eq!(old.key_id, "old");
        assert_eq!(new.key_id, "new");
    }

    #[test]
    fn rejects_raw_plaintext_sha256_values_and_malformed_expiry() {
        let invalid_digest = serde_json::json!({
            "worker-a": {
                "keys": [
                    {"key_id": "bad", "sha256": "plain-text", "expires_at": "2099-01-01T00:00:00Z"}
                ]
            }
        });
        assert!(parse_basic_keyring_json(&invalid_digest.to_string(), "worker").is_err());

        let bad_date = serde_json::json!({
            "worker-a": {
                "keys": [
                    {"key_id": "bad", "sha256": hex::encode([0_u8; 32]), "expires_at": "not-a-date"}
                ]
            }
        });
        assert!(parse_basic_keyring_json(&bad_date.to_string(), "worker").is_err());
    }

    #[test]
    fn rejects_duplicate_key_id_or_digest_within_identity() {
        let duplicate_key = serde_json::json!({
            "worker-a": {
                "keys": [
                    {"key_id": "old", "sha256": hex::encode([0_u8; 32]), "expires_at": "2099-01-01T00:00:00Z"},
                    {"key_id": "old", "sha256": hex::encode([1_u8; 32]), "expires_at": "2099-01-01T00:00:00Z"}
                ]
            }
        });
        assert!(parse_basic_keyring_json(&duplicate_key.to_string(), "worker").is_err());

        let duplicate_digest = serde_json::json!({
            "worker-a": {
                "keys": [
                    {"key_id": "old", "sha256": hex::encode([0_u8; 32]), "expires_at": "2099-01-01T00:00:00Z"},
                    {"key_id": "new", "sha256": hex::encode([0_u8; 32]), "expires_at": "2099-02-01T00:00:00Z"}
                ]
            }
        });
        assert!(parse_basic_keyring_json(&duplicate_digest.to_string(), "worker").is_err());
    }

    #[test]
    fn expired_key_is_rejected_by_matcher() {
        let old = "expired-worker-key";
        let json = serde_json::json!({
            "worker-a": {
                "keys": [
                    {
                        "key_id": "expired",
                        "sha256": hex::encode(Sha256::digest(old.as_bytes())),
                        "expires_at": "2000-01-01T00:00:00Z"
                    }
                ]
            }
        });
        let keyring = parse_basic_keyring_json(&json.to_string(), "worker").unwrap();
        assert!(
            identify_basic_key(&keyring, old, now("2001-01-01T00:00:00Z")).is_none(),
            "expired key must not authenticate"
        );
    }

    #[test]
    fn key_is_expired_at_its_expiry_timestamp() {
        let raw = "boundary-worker-key";
        let json = serde_json::json!({
            "worker-a": {
                "keys": [{
                    "key_id": "boundary",
                    "sha256": hex::encode(Sha256::digest(raw.as_bytes())),
                    "expires_at": "2099-01-01T00:00:00Z"
                }]
            }
        });
        let keyring = parse_basic_keyring_json(&json.to_string(), "worker").unwrap();
        assert!(identify_basic_key(&keyring, raw, now("2099-01-01T00:00:00Z")).is_none());
    }

    #[test]
    fn scheduler_scope_rejects_nonpositive_franchises_and_padded_targets() {
        let digest = hex::encode([3_u8; 32]);
        for (franchise_ids, target_worker_ids) in [
            (serde_json::json!([0]), serde_json::json!(["worker-a"])),
            (serde_json::json!([11]), serde_json::json!([" worker-a"])),
        ] {
            let json = serde_json::json!({
                "scheduler-a": {
                    "keys": [{
                        "key_id": "primary",
                        "sha256": digest,
                        "expires_at": "2099-01-01T00:00:00Z"
                    }],
                    "franchise_ids": franchise_ids,
                    "target_worker_ids": target_worker_ids,
                    "can_reconcile": false
                }
            });
            assert!(parse_scheduler_keyring_json(&json.to_string()).is_err());
        }
    }

    #[test]
    fn key_records_reject_unknown_fields() {
        let json = serde_json::json!({
            "worker-a": {
                "keys": [{
                    "key_id": "primary",
                    "sha256": hex::encode([4_u8; 32]),
                    "expires_at": "2099-01-01T00:00:00Z",
                    "plaintext": "must-not-be-ignored"
                }]
            }
        });
        assert!(parse_basic_keyring_json(&json.to_string(), "worker").is_err());
    }

    #[test]
    fn identify_matches_use_constant_time_digest_compare() {
        let first = Sha256::digest("first").to_vec();
        let second = Sha256::digest("second").to_vec();
        let json = serde_json::json!({
            "worker-a": {
                "keys": [
                    {"key_id": "first", "sha256": hex::encode(first), "expires_at": "2099-01-01T00:00:00Z"},
                    {"key_id": "second", "sha256": hex::encode(second), "expires_at": "2099-01-01T00:00:00Z"}
                ]
            }
        });
        let keyring = parse_basic_keyring_json(&json.to_string(), "worker").unwrap();
        assert!(identify_basic_key(&keyring, "first", now("2098-01-01T00:00:00Z")).is_some());
        assert!(identify_basic_key(&keyring, "wrong", now("2098-01-01T00:00:00Z")).is_none());
    }

    #[test]
    fn scheduler_claims_carry_identity_policy_fields() {
        let raw = "scheduler-key";
        let json = serde_json::json!({
            "scheduler-a": {
                "keys": [
                    {
                        "key_id": "primary",
                        "sha256": hex::encode(Sha256::digest(raw.as_bytes())),
                        "expires_at": "2099-01-01T00:00:00Z"
                    }
                ],
                "franchise_ids": [11, 12],
                "target_worker_ids": ["dev-alice-laptop", "prod-windows-01"],
                "can_reconcile": true
            }
        });

        let keyring = parse_scheduler_keyring_json(&json.to_string()).unwrap();
        let auth = identify_scheduler_key(&keyring, raw, now("2098-01-01T00:00:00Z")).unwrap();
        assert_eq!(auth.identity, "scheduler-a");
        assert_eq!(auth.key_id, "primary");
        assert!(auth.franchise_ids.contains(&11));
        assert!(auth.target_worker_ids.contains("dev-alice-laptop"));
        assert!(auth.can_reconcile);
    }

    #[test]
    fn rejects_same_digest_across_identies_within_basic_keyring() {
        let digest = "00".repeat(32);
        let json = serde_json::json!({
            "worker-a": {
                "keys": [
                    {"key_id": "first", "sha256": digest, "expires_at": "2099-01-01T00:00:00Z"}
                ]
            },
            "worker-b": {
                "keys": [
                    {"key_id": "first", "sha256": digest, "expires_at": "2099-01-01T00:00:00Z"}
                ]
            }
        });
        assert!(parse_basic_keyring_json(&json.to_string(), "worker").is_err());
    }

    #[test]
    fn rejects_duplicate_key_ids_within_identity_and_digest_reuse_across_roles() {
        let worker_json = serde_json::json!({
            "worker-a": {
                "keys": [
                    {
                        "key_id": "worker",
                        "sha256": hex::encode([0_u8; 32]),
                        "expires_at": "2099-01-01T00:00:00Z"
                    }
                ]
            }
        });
        let scheduler_json = serde_json::json!({
            "scheduler-a": {
                "keys": [
                    {
                        "key_id": "scheduler",
                        "sha256": hex::encode([0_u8; 32]),
                        "expires_at": "2099-01-01T00:00:00Z"
                    }
                ],
                "franchise_ids": [11],
                "target_worker_ids": ["dev-alice-laptop"],
                "can_reconcile": false
            }
        });
        let worker = parse_basic_keyring_json(&worker_json.to_string(), "worker").unwrap();
        let scheduler = parse_scheduler_keyring_json(&scheduler_json.to_string()).unwrap();
        let operator = parse_basic_keyring_json(
            &serde_json::json!({
                "ops-a": {
                    "keys": [{
                        "key_id": "ops",
                        "sha256": hex::encode([1_u8; 32]),
                        "expires_at": "2099-01-01T00:00:00Z"
                    }]
                }
            })
            .to_string(),
            "operator",
        )
        .unwrap();
        let readiness = parse_basic_keyring_json(
            &serde_json::json!({
                "readiness-a": {
                    "keys": [{
                        "key_id": "ready",
                        "sha256": hex::encode([2_u8; 32]),
                        "expires_at": "2099-01-01T00:00:00Z"
                    }]
                }
            })
            .to_string(),
            "readiness",
        )
        .unwrap();

        assert!(
            validate_cross_role_digest_uniqueness(&worker, &scheduler, &operator, &readiness)
                .is_err()
        );
    }
}
