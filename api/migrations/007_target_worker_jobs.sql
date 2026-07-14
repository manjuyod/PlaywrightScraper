DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM grade_scrape_jobs
        WHERE status IN ('queued', 'running')
    ) THEN
        RAISE EXCEPTION
            'Drain active jobs before adding target worker enforcement';
    END IF;
END $$;

ALTER TABLE grade_scrape_jobs
    ADD COLUMN target_worker_id TEXT;

ALTER TABLE grade_scrape_jobs
    ADD CONSTRAINT ck_grade_scrape_jobs_active_target_worker_id
    CHECK (
        status NOT IN ('queued', 'running')
        OR (target_worker_id IS NOT NULL AND BTRIM(target_worker_id) <> '')
    );
