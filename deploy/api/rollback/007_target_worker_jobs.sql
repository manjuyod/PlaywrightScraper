DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM grade_scrape_jobs
        WHERE status IN ('queued', 'running')
    ) THEN
        RAISE EXCEPTION
            'Cannot rollback target worker IDs while active jobs remain';
    END IF;
END $$;

ALTER TABLE grade_scrape_jobs
    DROP CONSTRAINT IF EXISTS ck_grade_scrape_jobs_active_target_worker_id;

ALTER TABLE grade_scrape_jobs
    DROP COLUMN IF EXISTS target_worker_id;
