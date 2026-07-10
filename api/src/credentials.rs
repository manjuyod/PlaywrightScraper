use std::collections::HashMap;

use aes_gcm::{
    aead::{rand_core::RngCore, Aead, OsRng, Payload},
    Aes256Gcm, KeyInit, Nonce,
};
use serde::{Deserialize, Serialize};
use thiserror::Error;
use zeroize::{Zeroize, ZeroizeOnDrop};

pub const ALTERNATE_CREDENTIALS_SCHEMA_VERSION: i16 = 1;

#[derive(Clone, Deserialize, Serialize, PartialEq, Eq, Zeroize, ZeroizeOnDrop)]
#[serde(deny_unknown_fields)]
pub struct AlternateCredentials {
    pub username: String,
    pub password: String,
}

#[derive(Clone, PartialEq, Eq)]
pub struct EncryptedCredentialEnvelope {
    pub version: i16,
    pub key_id: String,
    pub nonce: Vec<u8>,
    pub ciphertext: Vec<u8>,
}

#[derive(Clone)]
pub struct AlternateCredentialKeyring {
    active_key_id: String,
    keys: HashMap<String, [u8; 32]>,
    environment: String,
}

#[derive(Debug, Error, PartialEq, Eq)]
pub enum CredentialCryptoError {
    #[error("invalid alternate credential keyring")]
    InvalidKeyring,
    #[error("invalid alternate credential envelope")]
    InvalidEnvelope,
    #[error("alternate credential key is unavailable")]
    KeyUnavailable,
    #[error("alternate credential encryption failed")]
    EncryptFailed,
    #[error("alternate credential decryption failed")]
    DecryptFailed,
}

impl AlternateCredentialKeyring {
    pub fn new(
        active_key_id: &str,
        keys: HashMap<String, [u8; 32]>,
        environment: &str,
    ) -> Result<Self, CredentialCryptoError> {
        let valid_key_id = |key_id: &str| {
            !key_id.is_empty()
                && key_id.len() <= 64
                && key_id
                    .chars()
                    .next()
                    .is_some_and(|character| character.is_ascii_alphanumeric())
                && key_id.chars().all(|character| {
                    character.is_ascii_alphanumeric() || matches!(character, '.' | '_' | '-')
                })
        };
        if keys.is_empty()
            || !keys.contains_key(active_key_id)
            || !valid_key_id(active_key_id)
            || keys.keys().any(|key_id| !valid_key_id(key_id))
            || environment.is_empty()
            || environment.trim() != environment
        {
            return Err(CredentialCryptoError::InvalidKeyring);
        }
        Ok(Self {
            active_key_id: active_key_id.to_string(),
            keys,
            environment: environment.to_string(),
        })
    }

    pub fn active_key_id(&self) -> &str {
        &self.active_key_id
    }

    pub fn encrypt(
        &self,
        crmstudentid: i64,
        credentials: &AlternateCredentials,
    ) -> Result<EncryptedCredentialEnvelope, CredentialCryptoError> {
        if crmstudentid <= 0
            || credentials.username.trim().is_empty()
            || credentials.password.trim().is_empty()
        {
            return Err(CredentialCryptoError::EncryptFailed);
        }
        let key = self
            .keys
            .get(&self.active_key_id)
            .ok_or(CredentialCryptoError::KeyUnavailable)?;
        let cipher =
            Aes256Gcm::new_from_slice(key).map_err(|_| CredentialCryptoError::InvalidKeyring)?;
        let mut nonce = [0_u8; 12];
        OsRng.fill_bytes(&mut nonce);
        let mut plaintext =
            serde_json::to_vec(credentials).map_err(|_| CredentialCryptoError::EncryptFailed)?;
        let aad = self.aad(crmstudentid);
        let encrypted = cipher.encrypt(
            Nonce::from_slice(&nonce),
            Payload {
                msg: &plaintext,
                aad: aad.as_bytes(),
            },
        );
        plaintext.zeroize();
        let ciphertext = encrypted.map_err(|_| CredentialCryptoError::EncryptFailed)?;
        Ok(EncryptedCredentialEnvelope {
            version: ALTERNATE_CREDENTIALS_SCHEMA_VERSION,
            key_id: self.active_key_id.clone(),
            nonce: nonce.to_vec(),
            ciphertext,
        })
    }

    pub fn decrypt(
        &self,
        crmstudentid: i64,
        envelope: &EncryptedCredentialEnvelope,
    ) -> Result<AlternateCredentials, CredentialCryptoError> {
        if crmstudentid <= 0
            || envelope.version != ALTERNATE_CREDENTIALS_SCHEMA_VERSION
            || envelope.nonce.len() != 12
            || envelope.ciphertext.len() < 16
        {
            return Err(CredentialCryptoError::InvalidEnvelope);
        }
        let key = self
            .keys
            .get(&envelope.key_id)
            .ok_or(CredentialCryptoError::KeyUnavailable)?;
        let cipher =
            Aes256Gcm::new_from_slice(key).map_err(|_| CredentialCryptoError::InvalidKeyring)?;
        let aad = self.aad(crmstudentid);
        let mut plaintext = cipher
            .decrypt(
                Nonce::from_slice(&envelope.nonce),
                Payload {
                    msg: &envelope.ciphertext,
                    aad: aad.as_bytes(),
                },
            )
            .map_err(|_| CredentialCryptoError::DecryptFailed)?;
        let credentials = serde_json::from_slice::<AlternateCredentials>(&plaintext)
            .map_err(|_| CredentialCryptoError::DecryptFailed);
        plaintext.zeroize();
        let credentials = credentials?;
        if credentials.username.trim().is_empty() || credentials.password.trim().is_empty() {
            return Err(CredentialCryptoError::DecryptFailed);
        }
        Ok(credentials)
    }

    fn aad(&self, crmstudentid: i64) -> String {
        format!(
            "environment={};schema=students_grades_20262027;version={};field=alternate_credentials;crmstudentid={crmstudentid}",
            self.environment, ALTERNATE_CREDENTIALS_SCHEMA_VERSION
        )
    }
}

#[cfg(test)]
pub fn test_keyring() -> AlternateCredentialKeyring {
    AlternateCredentialKeyring::new(
        "test-key",
        HashMap::from([("test-key".into(), [7_u8; 32])]),
        "test",
    )
    .expect("static test keyring is valid")
}

#[cfg(test)]
mod tests {
    use std::collections::HashMap;

    use super::{AlternateCredentialKeyring, AlternateCredentials};

    fn keyring() -> AlternateCredentialKeyring {
        AlternateCredentialKeyring::new(
            "key-2026-07",
            HashMap::from([
                ("key-2026-07".into(), [7_u8; 32]),
                ("key-previous".into(), [3_u8; 32]),
            ]),
            "test",
        )
        .unwrap()
    }

    #[test]
    fn envelope_round_trip_preserves_both_fields() {
        let keyring = keyring();
        let credentials = AlternateCredentials {
            username: "alternate-user".into(),
            password: "alternate-password".into(),
        };

        let envelope = keyring.encrypt(42, &credentials).unwrap();
        let decrypted = keyring.decrypt(42, &envelope).unwrap();

        assert!(decrypted == credentials);
        assert_eq!(envelope.key_id, "key-2026-07");
        assert_eq!(envelope.nonce.len(), 12);
        assert!(!envelope.ciphertext.is_empty());
    }

    #[test]
    fn aad_rejects_swapping_an_envelope_between_students() {
        let keyring = keyring();
        let envelope = keyring
            .encrypt(
                42,
                &AlternateCredentials {
                    username: "user".into(),
                    password: "password".into(),
                },
            )
            .unwrap();

        assert!(keyring.decrypt(43, &envelope).is_err());
    }

    #[test]
    fn retired_or_wrong_keys_cannot_decrypt() {
        let envelope = keyring()
            .encrypt(
                42,
                &AlternateCredentials {
                    username: "user".into(),
                    password: "password".into(),
                },
            )
            .unwrap();
        let retired = AlternateCredentialKeyring::new(
            "new-key",
            HashMap::from([("new-key".into(), [9_u8; 32])]),
            "test",
        )
        .unwrap();

        assert!(retired.decrypt(42, &envelope).is_err());
    }
}
