-- Read-only inventory. Run manually against Neon before reviewing 001_runner_boundary.sql.
SELECT
    expected.table_name,
    to_regclass('public.' || expected.table_name) AS relation_name,
    COALESCE(stats.n_live_tup, 0) AS estimated_rows
FROM (
    VALUES
        ('students_grades_20262027'),
        ('grade_scrape_jobs'),
        ('grade_scrape_job_events'),
        ('grade_scrape_results'),
        ('dashboard_replay_nonces')
) AS expected(table_name)
LEFT JOIN pg_stat_user_tables AS stats
    ON stats.schemaname = 'public'
   AND stats.relname = expected.table_name
ORDER BY expected.table_name;

SELECT
    table_name,
    COUNT(*) AS column_count,
    string_agg(column_name || ' ' || data_type, ', ' ORDER BY ordinal_position) AS columns
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name IN (
      'students_grades_20262027',
      'grade_scrape_jobs',
      'grade_scrape_job_events',
      'grade_scrape_results',
      'dashboard_replay_nonces'
  )
GROUP BY table_name
ORDER BY table_name;

SELECT
    relation.relname AS table_name,
    constraint_row.conname AS constraint_name,
    pg_get_constraintdef(constraint_row.oid) AS definition
FROM pg_constraint AS constraint_row
JOIN pg_class AS relation ON relation.oid = constraint_row.conrelid
JOIN pg_namespace AS namespace_row ON namespace_row.oid = relation.relnamespace
WHERE namespace_row.nspname = 'public'
  AND relation.relname IN (
      'students_grades_20262027',
      'grade_scrape_jobs',
      'grade_scrape_job_events',
      'grade_scrape_results'
  )
ORDER BY relation.relname, constraint_row.conname;
