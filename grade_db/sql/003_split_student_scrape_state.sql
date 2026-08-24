BEGIN;

ALTER TABLE students_grades_20262027
    ADD COLUMN IF NOT EXISTS primary_agenda JSONB NOT NULL
        DEFAULT '{"portal":null,"weeks":{}}'::jsonb,
    ADD COLUMN IF NOT EXISTS secondary_agenda JSONB NOT NULL
        DEFAULT '{"portal":null,"weeks":{}}'::jsonb,
    ADD COLUMN IF NOT EXISTS grade_status TEXT NOT NULL DEFAULT 'never',
    ADD COLUMN IF NOT EXISTS primary_agenda_status TEXT NOT NULL DEFAULT 'never',
    ADD COLUMN IF NOT EXISTS secondary_agenda_status TEXT NOT NULL DEFAULT 'never',
    ADD COLUMN IF NOT EXISTS grade_updated_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS primary_agenda_updated_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS secondary_agenda_updated_at TIMESTAMPTZ NULL;

DO $$
BEGIN
    IF (
        SELECT COUNT(*) = 4 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'students_grades_20262027'
          AND column_name IN ('weekly_agenda', 'status', 'error_msg', 'updated_at')
    ) THEN
        -- Keep this as one UPDATE. The legacy BEFORE UPDATE trigger changes
        -- updated_at, while expressions in this statement still read its
        -- original value for the channel-specific timestamps.
        EXECUTE $migration$
            UPDATE students_grades_20262027
            SET primary_agenda = CASE
                    WHEN primary_agenda = '{"portal":null,"weeks":{}}'::jsonb
                    THEN COALESCE(
                        weekly_agenda->'agenda1',
                        '{"portal":null,"weeks":{}}'::jsonb
                    )
                    ELSE primary_agenda
                END,
                secondary_agenda = CASE
                    WHEN secondary_agenda = '{"portal":null,"weeks":{}}'::jsonb
                    THEN COALESCE(
                        weekly_agenda->'agenda2',
                        '{"portal":null,"weeks":{}}'::jsonb
                    )
                    ELSE secondary_agenda
                END,
                primary_agenda_status = CASE
                    WHEN primary_agenda_status <> 'never' THEN primary_agenda_status
                    WHEN status = 'error'
                        AND error_msg LIKE 'agenda1\_%\_failed' ESCAPE '\'
                    THEN 'scrape_failed'
                    WHEN weekly_agenda ? 'agenda1' THEN 'synced'
                    ELSE 'never'
                END,
                secondary_agenda_status = CASE
                    WHEN secondary_agenda_status <> 'never' THEN secondary_agenda_status
                    WHEN status = 'error'
                        AND error_msg LIKE 'agenda2\_%\_failed' ESCAPE '\'
                    THEN 'scrape_failed'
                    WHEN weekly_agenda ? 'agenda2' THEN 'synced'
                    ELSE 'never'
                END,
                grade_status = CASE
                    WHEN grade_status <> 'never' THEN grade_status
                    WHEN status = 'error'
                        AND error_msg IN ('bad_login', 'no_grades', 'scrape_failed')
                    THEN error_msg
                    WHEN COALESCE(weeklydata, '{}'::jsonb) <> '{}'::jsonb THEN 'synced'
                    ELSE 'never'
                END,
                grade_updated_at = CASE
                    WHEN grade_updated_at IS NOT NULL THEN grade_updated_at
                    WHEN grade_status <> 'never'
                        OR COALESCE(weeklydata, '{}'::jsonb) <> '{}'::jsonb
                        OR (
                            status = 'error'
                            AND error_msg IN ('bad_login', 'no_grades', 'scrape_failed')
                        )
                    THEN updated_at
                    ELSE NULL
                END,
                primary_agenda_updated_at = CASE
                    WHEN primary_agenda_updated_at IS NOT NULL
                    THEN primary_agenda_updated_at
                    WHEN primary_agenda_status <> 'never'
                        OR weekly_agenda ? 'agenda1'
                        OR (
                            status = 'error'
                            AND error_msg LIKE 'agenda1\_%\_failed' ESCAPE '\'
                        )
                    THEN updated_at
                    ELSE NULL
                END,
                secondary_agenda_updated_at = CASE
                    WHEN secondary_agenda_updated_at IS NOT NULL
                    THEN secondary_agenda_updated_at
                    WHEN secondary_agenda_status <> 'never'
                        OR weekly_agenda ? 'agenda2'
                        OR (
                            status = 'error'
                            AND error_msg LIKE 'agenda2\_%\_failed' ESCAPE '\'
                        )
                    THEN updated_at
                    ELSE NULL
                END
        $migration$;
    END IF;
END
$$;

ALTER TABLE students_grades_20262027
    DROP CONSTRAINT IF EXISTS ck_students_grades_grade_status,
    DROP CONSTRAINT IF EXISTS ck_students_grades_primary_agenda_status,
    DROP CONSTRAINT IF EXISTS ck_students_grades_secondary_agenda_status;

ALTER TABLE students_grades_20262027
    ADD CONSTRAINT ck_students_grades_grade_status
        CHECK (grade_status ~ '^[a-z][a-z0-9_-]{0,63}$'),
    ADD CONSTRAINT ck_students_grades_primary_agenda_status
        CHECK (primary_agenda_status ~ '^[a-z][a-z0-9_-]{0,63}$'),
    ADD CONSTRAINT ck_students_grades_secondary_agenda_status
        CHECK (secondary_agenda_status ~ '^[a-z][a-z0-9_-]{0,63}$');

ALTER TABLE grade_scrape_jobs
    ADD COLUMN IF NOT EXISTS failure_code TEXT NULL;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'grade_scrape_jobs'
          AND column_name = 'error_msg'
    ) THEN
        EXECUTE $migration$
            UPDATE grade_scrape_jobs
            SET failure_code = COALESCE(failure_code, error_msg)
            WHERE error_msg IS NOT NULL
        $migration$;
    END IF;
END
$$;

COMMIT;
