ALTER TABLE students_grades_20262027
    ADD COLUMN IF NOT EXISTS alternate_credentials_version SMALLINT NULL,
    ADD COLUMN IF NOT EXISTS alternate_credentials_key_id TEXT NULL,
    ADD COLUMN IF NOT EXISTS alternate_credentials_nonce BYTEA NULL,
    ADD COLUMN IF NOT EXISTS alternate_credentials_ciphertext BYTEA NULL;

ALTER TABLE students_grades_20262027
    DROP CONSTRAINT IF EXISTS ck_students_grades_alternate_credentials_envelope;

ALTER TABLE students_grades_20262027
    ADD CONSTRAINT ck_students_grades_alternate_credentials_envelope
    CHECK (
        num_nonnulls(
            alternate_credentials_version,
            alternate_credentials_key_id,
            alternate_credentials_nonce,
            alternate_credentials_ciphertext
        ) = 0
        OR (
            num_nonnulls(
                alternate_credentials_version,
                alternate_credentials_key_id,
                alternate_credentials_nonce,
                alternate_credentials_ciphertext
            ) = 4
            AND alternate_credentials_version = 1
            AND alternate_credentials_key_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'
            AND octet_length(alternate_credentials_nonce) = 12
            AND octet_length(alternate_credentials_ciphertext) >= 16
        )
    );

CREATE INDEX IF NOT EXISTS idx_students_grades_alternate_credentials_key_id
    ON students_grades_20262027 (alternate_credentials_key_id)
    WHERE alternate_credentials_key_id IS NOT NULL;
