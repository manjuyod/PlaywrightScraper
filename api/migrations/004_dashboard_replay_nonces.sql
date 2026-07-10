CREATE TABLE IF NOT EXISTS dashboard_replay_nonces (
    identity_hash BYTEA NOT NULL
        CONSTRAINT ck_dashboard_replay_identity_hash_length
        CHECK (octet_length(identity_hash) = 32),
    nonce UUID NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    claimed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (identity_hash, nonce)
);

CREATE INDEX IF NOT EXISTS idx_dashboard_replay_nonces_expires_at
    ON dashboard_replay_nonces (expires_at);

ALTER TABLE grade_scrape_jobs
    ADD COLUMN IF NOT EXISTS scheduler_identity TEXT NULL,
    ADD COLUMN IF NOT EXISTS scheduler_idempotency_key UUID NULL,
    ADD COLUMN IF NOT EXISTS scheduler_request_hash BYTEA NULL;

ALTER TABLE grade_scrape_jobs
    DROP CONSTRAINT IF EXISTS ck_grade_scrape_jobs_scheduler_request_hash_length;

ALTER TABLE grade_scrape_jobs
    ADD CONSTRAINT ck_grade_scrape_jobs_scheduler_request_hash_length
    CHECK (
        scheduler_request_hash IS NULL
        OR octet_length(scheduler_request_hash) = 32
    );

CREATE UNIQUE INDEX IF NOT EXISTS uq_grade_scrape_jobs_scheduler_idempotency
    ON grade_scrape_jobs (scheduler_identity, scheduler_idempotency_key)
    WHERE scheduler_identity IS NOT NULL
      AND scheduler_idempotency_key IS NOT NULL;
