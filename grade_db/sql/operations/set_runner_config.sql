-- Human-reviewed template. Replace psql variables before running.
BEGIN;

UPDATE students_grades_20262027
SET portal2 = NULLIF(:'portal2', ''),
    p2username = NULLIF(:'p2username', ''),
    p2password = NULLIF(:'p2password', ''),
    portal = NULLIF(:'portal_override', ''),
    track_agenda = :'track_agenda'::boolean,
    auth_type = NULLIF(:'auth_type', ''),
    auth_answers = :'auth_answers_json'::jsonb,
    updated_at = now()
WHERE crmstudentid = :'crmstudentid'::bigint;

COMMIT;
