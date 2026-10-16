-- Run once on the database (needs permission to create functions/triggers).
-- Sends a NOTIFY on channel "job_queued" whenever a job's status becomes 'Queued'.
-- The worker also polls, so this is only for instant wake-up.

CREATE OR REPLACE FUNCTION notify_job_queued() RETURNS trigger AS $$
BEGIN
    PERFORM pg_notify('job_queued', NEW.id::text);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS job_queued_on_update ON job_app_job;
CREATE TRIGGER job_queued_on_update
AFTER UPDATE OF status ON job_app_job
FOR EACH ROW
WHEN (lower(NEW.status) = 'queued' AND OLD.status IS DISTINCT FROM NEW.status)
EXECUTE FUNCTION notify_job_queued();

DROP TRIGGER IF EXISTS job_queued_on_insert ON job_app_job;
CREATE TRIGGER job_queued_on_insert
AFTER INSERT ON job_app_job
FOR EACH ROW
WHEN (lower(NEW.status) = 'queued')
EXECUTE FUNCTION notify_job_queued();
