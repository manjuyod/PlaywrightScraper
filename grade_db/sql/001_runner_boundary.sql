BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS students_grades_20262027 (
    uuid UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    crmstudentid BIGINT NOT NULL UNIQUE,
    portal2 TEXT NULL,
    p2username TEXT NULL,
    p2password TEXT NULL,
    weeklydata JSONB NOT NULL DEFAULT '{}'::jsonb,
    weekly_agenda JSONB NOT NULL DEFAULT '{}'::jsonb,
    portal TEXT NULL,
    passwordgood BOOLEAN NULL,
    status TEXT NOT NULL DEFAULT 'never',
    error_msg TEXT NULL,
    track_agenda BOOLEAN NOT NULL DEFAULT FALSE,
    auth_type TEXT NULL,
    auth_answers JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE students_grades_20262027
    ALTER COLUMN crmstudentid TYPE BIGINT USING crmstudentid::BIGINT,
    ADD COLUMN IF NOT EXISTS portal2 TEXT NULL,
    ADD COLUMN IF NOT EXISTS p2username TEXT NULL,
    ADD COLUMN IF NOT EXISTS p2password TEXT NULL,
    ADD COLUMN IF NOT EXISTS weeklydata JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS weekly_agenda JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS portal TEXT NULL,
    ADD COLUMN IF NOT EXISTS passwordgood BOOLEAN NULL,
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'never',
    ADD COLUMN IF NOT EXISTS error_msg TEXT NULL,
    ADD COLUMN IF NOT EXISTS track_agenda BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS auth_type TEXT NULL,
    ADD COLUMN IF NOT EXISTS auth_answers JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

ALTER TABLE students_grades_20262027
    DROP CONSTRAINT IF EXISTS ck_students_grades_no_plaintext_alternate_credentials;

CREATE UNIQUE INDEX IF NOT EXISTS uq_students_grades_20262027_crmstudentid
    ON students_grades_20262027 (crmstudentid);

CREATE INDEX IF NOT EXISTS idx_students_grades_20262027_status
    ON students_grades_20262027 (status);

CREATE OR REPLACE FUNCTION set_students_grades_20262027_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_students_grades_20262027_updated_at
    ON students_grades_20262027;

CREATE TRIGGER trg_students_grades_20262027_updated_at
BEFORE UPDATE ON students_grades_20262027
FOR EACH ROW
EXECUTE FUNCTION set_students_grades_20262027_updated_at();

CREATE TABLE IF NOT EXISTS grade_scrape_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    franchise_id INTEGER NULL,
    student_id BIGINT NULL,
    runner_id TEXT NOT NULL,
    lease_token UUID NOT NULL,
    lease_expires_at TIMESTAMPTZ NOT NULL,
    progress JSONB NOT NULL DEFAULT '{}'::jsonb,
    summary JSONB NULL,
    error_msg TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ NULL,
    CONSTRAINT ck_grade_scrape_jobs_kind CHECK (kind IN ('grade', 'agenda')),
    CONSTRAINT ck_grade_scrape_jobs_status CHECK (status IN ('running', 'complete', 'failed'))
);

ALTER TABLE grade_scrape_jobs
    ADD COLUMN IF NOT EXISTS runner_id TEXT,
    ADD COLUMN IF NOT EXISTS lease_token UUID NULL,
    ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS progress JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS summary JSONB NULL,
    ADD COLUMN IF NOT EXISTS error_msg TEXT NULL,
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ NULL;

ALTER TABLE grade_scrape_jobs
    ALTER COLUMN franchise_id DROP NOT NULL,
    DROP CONSTRAINT IF EXISTS ck_grade_scrape_jobs_active_target_worker_id;

DROP INDEX IF EXISTS uq_grade_scrape_jobs_active;

UPDATE grade_scrape_jobs
SET status = 'failed',
    error_msg = COALESCE(error_msg, 'retired_legacy_job'),
    completed_at = COALESCE(completed_at, now()),
    updated_at = now()
WHERE status IN ('queued', 'running')
  AND (runner_id IS NULL OR lease_token IS NULL OR lease_expires_at IS NULL);

CREATE INDEX IF NOT EXISTS idx_grade_scrape_jobs_status_lease
    ON grade_scrape_jobs (status, lease_expires_at);

CREATE UNIQUE INDEX IF NOT EXISTS uq_grade_scrape_jobs_active_scope
    ON grade_scrape_jobs (kind, COALESCE(franchise_id, 0))
    WHERE status = 'running';

CREATE TABLE IF NOT EXISTS grade_scrape_job_events (
    id BIGSERIAL PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES grade_scrape_jobs (id),
    level TEXT NOT NULL DEFAULT 'info',
    code TEXT NOT NULL,
    message TEXT NOT NULL,
    crmstudentid BIGINT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE grade_scrape_job_events
    ADD COLUMN IF NOT EXISTS code TEXT,
    ADD COLUMN IF NOT EXISTS message TEXT,
    ADD COLUMN IF NOT EXISTS crmstudentid BIGINT NULL,
    ADD COLUMN IF NOT EXISTS payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();

UPDATE grade_scrape_job_events
SET code = COALESCE(NULLIF(code, ''), 'legacy_event');

UPDATE grade_scrape_job_events
SET message = COALESCE(NULLIF(message, ''), code, 'legacy_event');

ALTER TABLE grade_scrape_job_events
    ALTER COLUMN code SET NOT NULL,
    ALTER COLUMN message SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_grade_scrape_job_events_job_created
    ON grade_scrape_job_events (job_id, created_at);

CREATE TABLE IF NOT EXISTS grade_scrape_results (
    id BIGSERIAL PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES grade_scrape_jobs (id),
    crmstudentid BIGINT NOT NULL,
    idempotency_key UUID NOT NULL,
    payload JSONB NOT NULL,
    applied BOOLEAN NOT NULL DEFAULT FALSE,
    rejection_code TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE grade_scrape_results
    ADD COLUMN IF NOT EXISTS crmstudentid BIGINT NULL,
    ADD COLUMN IF NOT EXISTS idempotency_key UUID NULL,
    ADD COLUMN IF NOT EXISTS payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS applied BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS rejection_code TEXT NULL,
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();

CREATE UNIQUE INDEX IF NOT EXISTS uq_grade_scrape_results_job_idempotency_key
    ON grade_scrape_results (job_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_grade_scrape_results_student_created
    ON grade_scrape_results (crmstudentid, created_at DESC);

COMMIT;
