ALTER TABLE grade_scrape_results
    ADD COLUMN IF NOT EXISTS idempotency_key UUID;

CREATE UNIQUE INDEX IF NOT EXISTS uq_grade_scrape_results_job_idempotency_key
    ON grade_scrape_results (job_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
