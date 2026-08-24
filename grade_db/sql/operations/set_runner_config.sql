-- Human-reviewed template. Replace psql variables before running.
BEGIN;

UPDATE students_grades_20262027
SET portal = NULLIF(:'portal_override', ''),
    track_agenda = :'track_agenda'::boolean,
    auth_type = NULLIF(:'auth_type', ''),
    auth_answers = :'auth_answers_json'::jsonb
WHERE crmstudentid = :'crmstudentid'::bigint;

COMMIT;
