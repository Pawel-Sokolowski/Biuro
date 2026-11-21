-- Run with psql as the migration/schema owner after creating a dedicated login role.
-- Supply schema and agent_role with -v. No password is stored in this file.
\set ON_ERROR_STOP on
BEGIN;
GRANT USAGE ON SCHEMA :"schema" TO :"agent_role";
GRANT EXECUTE ON FUNCTION :"schema".platnik_gui_claim() TO :"agent_role";
GRANT EXECUTE ON FUNCTION :"schema".platnik_gui_finish(uuid, uuid, jsonb, text) TO :"agent_role";
COMMIT;
