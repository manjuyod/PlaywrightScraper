-- Human-reviewed cleanup. Deploy the CRM-backed grade-db executable first.
BEGIN;

ALTER TABLE students_grades_20262027
    DROP CONSTRAINT IF EXISTS ck_students_grades_no_plaintext_alternate_credentials,
    DROP COLUMN IF EXISTS portal2,
    DROP COLUMN IF EXISTS p2username,
    DROP COLUMN IF EXISTS p2password;

COMMIT;
