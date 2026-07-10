CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS students_grades_20262027 (
    uuid UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    crmstudentid INTEGER NOT NULL UNIQUE,

    portal2 TEXT NULL,
    p2username TEXT NULL,
    p2password TEXT NULL,

    yearstart INTEGER NULL,
    yearend INTEGER NULL,
    weeklydata JSONB NULL DEFAULT '{}'::jsonb,
    portal TEXT NULL,

    passwordgood BOOLEAN NULL,
    status TEXT NULL,
    error_msg TEXT NULL,
    track_agenda BOOLEAN NULL DEFAULT FALSE,
    weekly_agenda JSONB NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_students_grades_20262027_crmstudentid
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

DO $$
BEGIN
    IF to_regclass('public.student') IS NOT NULL THEN
        INSERT INTO students_grades_20262027 (
            crmstudentid,
            portal2,
            p2username,
            p2password,
            yearstart,
            yearend,
            weeklydata,
            portal,
            passwordgood,
            status,
            error_msg,
            track_agenda,
            weekly_agenda,
            created_at,
            updated_at
        )
        SELECT
            s.id,
            s.portal2,
            s.p2username,
            s.p2password,
            CASE WHEN s.yearstart::text ~ '^[0-9]{4}$' THEN s.yearstart::integer ELSE NULL END,
            CASE WHEN s.yearend::text ~ '^[0-9]{4}$' THEN s.yearend::integer ELSE NULL END,
            COALESCE(s.weeklydata, '{}'::jsonb),
            s.portal,
            CASE
                WHEN s.passwordgood IS NULL THEN NULL
                WHEN lower(s.passwordgood::text) IN ('1', 't', 'true', 'yes') THEN TRUE
                ELSE FALSE
            END,
            s.status,
            s.error_msg,
            COALESCE(s.track_agenda, FALSE),
            COALESCE(s.weekly_agenda, '{}'::jsonb),
            COALESCE(s.created_at, now()),
            COALESCE(s.updated_at, now())
        FROM student s
        ON CONFLICT (crmstudentid) DO NOTHING;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS grade_scrape_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind TEXT NOT NULL DEFAULT 'grade',
    status TEXT NOT NULL DEFAULT 'queued',
    franchise_id INTEGER NOT NULL,
    student_id BIGINT NULL,
    worker_id TEXT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    heartbeat JSONB NULL,
    error_msg TEXT NULL,
    completed_payload JSONB NULL,
    created_by_user TEXT NULL,
    created_by_role INTEGER NULL,
    created_by_franchise_id INTEGER NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ NULL,
    completed_at TIMESTAMPTZ NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_grade_scrape_jobs_active
    ON grade_scrape_jobs (franchise_id, kind)
    WHERE status IN ('queued', 'running');

CREATE INDEX IF NOT EXISTS idx_grade_scrape_jobs_status
    ON grade_scrape_jobs (status);

CREATE INDEX IF NOT EXISTS idx_grade_scrape_jobs_franchise
    ON grade_scrape_jobs (franchise_id);

CREATE INDEX IF NOT EXISTS idx_grade_scrape_jobs_created_at
    ON grade_scrape_jobs (created_at DESC);

CREATE TABLE IF NOT EXISTS grade_scrape_job_events (
    id BIGSERIAL PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES grade_scrape_jobs (id) ON DELETE CASCADE,
    level TEXT NOT NULL DEFAULT 'info',
    message TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_grade_scrape_job_events_job_id
    ON grade_scrape_job_events (job_id, created_at);

CREATE TABLE IF NOT EXISTS grade_scrape_results (
    id BIGSERIAL PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES grade_scrape_jobs (id) ON DELETE CASCADE,
    crmstudentid BIGINT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_grade_scrape_results_job_id
    ON grade_scrape_results (job_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_grade_scrape_results_crmstudentid
    ON grade_scrape_results (crmstudentid);
