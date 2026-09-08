BEGIN;

DROP INDEX IF EXISTS idx_students_grades_20262027_status;
DROP TRIGGER IF EXISTS trg_students_grades_20262027_updated_at
    ON students_grades_20262027;
DROP FUNCTION IF EXISTS set_students_grades_20262027_updated_at();

ALTER TABLE students_grades_20262027
    DROP COLUMN IF EXISTS weekly_agenda,
    DROP COLUMN IF EXISTS status,
    DROP COLUMN IF EXISTS error_msg,
    DROP COLUMN IF EXISTS updated_at;

ALTER TABLE grade_scrape_jobs
    DROP COLUMN IF EXISTS error_msg;

COMMIT;
