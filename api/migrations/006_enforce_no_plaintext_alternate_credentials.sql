DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM students_grades_20262027
        WHERE (
            NULLIF(BTRIM(p2username), '') IS NOT NULL
            OR NULLIF(BTRIM(p2password), '') IS NOT NULL
        )
        AND num_nonnulls(
            alternate_credentials_version,
            alternate_credentials_key_id,
            alternate_credentials_nonce,
            alternate_credentials_ciphertext
        ) <> 4
    ) THEN
        RAISE EXCEPTION
            'plaintext alternate credentials remain without a verified encrypted envelope';
    END IF;
END $$;

UPDATE students_grades_20262027
SET p2username = NULL,
    p2password = NULL
WHERE p2username IS NOT NULL
   OR p2password IS NOT NULL;

ALTER TABLE students_grades_20262027
    DROP CONSTRAINT IF EXISTS ck_students_grades_no_plaintext_alternate_credentials;

ALTER TABLE students_grades_20262027
    ADD CONSTRAINT ck_students_grades_no_plaintext_alternate_credentials
    CHECK (p2username IS NULL AND p2password IS NULL);
