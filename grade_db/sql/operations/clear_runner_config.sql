-- track_agenda is deprecated compatibility data; it does not control agenda runs.
-- Human-reviewed template. Replace crmstudentid before running.
BEGIN;

UPDATE students_grades_20262027
SET portal = NULL,
    track_agenda = FALSE,
    auth_type = NULL,
    auth_answers = '[]'::jsonb
WHERE crmstudentid = :'crmstudentid'::bigint;

COMMIT;
