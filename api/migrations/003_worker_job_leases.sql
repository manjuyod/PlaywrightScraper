ALTER TABLE grade_scrape_jobs
    ADD COLUMN IF NOT EXISTS lease_token UUID NULL,
    ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0;

UPDATE grade_scrape_jobs
SET lease_expires_at = NOW()
WHERE status = 'running'
  AND lease_expires_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_grade_scrape_jobs_lease_reclaim
    ON grade_scrape_jobs (status, lease_expires_at, created_at)
    WHERE status = 'running';
