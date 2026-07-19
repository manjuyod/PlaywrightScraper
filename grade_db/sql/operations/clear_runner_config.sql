-- Human-reviewed template. Replace crmstudentid before running.
BEGIN;

UPDATE students_grades_20262027
SET portal2 = NULL,
    p2username = NULL,
    p2password = NULL,
    portal = NULL,
    track_agenda = FALSE,
    auth_type = NULL,
    auth_answers = '[]'::jsonb,
    updated_at = now()
WHERE crmstudentid = :'crmstudentid'::bigint;

COMMIT;
