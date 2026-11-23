"""Separate, leased queue for the interactive Windows Płatnik agent."""
from psycopg import sql

VERSION = '20261121_08'


def apply(conn):
    schema = conn.execute('SELECT current_schema()').fetchone()[0]
    conn.execute("""
        CREATE TABLE IF NOT EXISTS platnik_gui_checks (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            company_id integer NOT NULL REFERENCES firmy(id) ON DELETE CASCADE,
            actor_id integer,
            nip text NOT NULL CHECK (nip ~ '^[0-9]{10}$'),
            period text NOT NULL CHECK (period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'),
            state text NOT NULL DEFAULT 'queued'
                CHECK (state IN ('queued', 'running', 'completed', 'failed')),
            requested_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            started_at timestamptz,
            finished_at timestamptz,
            lease_id uuid,
            lease_until timestamptz,
            attempts integer NOT NULL DEFAULT 0,
            error_code text,
            findings jsonb NOT NULL DEFAULT '[]'::jsonb,
            CHECK ((lease_id IS NOT NULL) = (state = 'running')),
            CHECK ((lease_until IS NOT NULL) = (state = 'running'))
        )
    """)
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS platnik_gui_checks_open_scope
            ON platnik_gui_checks(company_id, nip, period) WHERE state IN ('queued', 'running')
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS platnik_gui_checks_period
            ON platnik_gui_checks(period, company_id, nip, requested_at DESC)
    """)
    conn.execute(sql.SQL("""
        CREATE OR REPLACE FUNCTION platnik_gui_claim()
        RETURNS TABLE(job_id uuid, lease uuid, payer_nip text, reporting_month text)
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path TO pg_catalog, {}, pg_temp
        AS $$
        BEGIN
            IF NOT pg_try_advisory_xact_lock('platnik_gui_checks'::regclass::oid::bigint) THEN
                RETURN;
            END IF;
            UPDATE platnik_gui_checks
            SET state = CASE WHEN attempts < 3 THEN 'queued' ELSE 'failed' END,
                error_code = CASE WHEN attempts >= 3 THEN 'LeaseExpired' END,
                finished_at = CASE WHEN attempts >= 3 THEN clock_timestamp() END,
                lease_id = NULL, lease_until = NULL
            WHERE state = 'running' AND lease_until <= clock_timestamp();
            IF EXISTS (SELECT FROM platnik_gui_checks WHERE state = 'running') THEN
                RETURN;
            END IF;
            RETURN QUERY
            WITH next AS (
                SELECT id FROM platnik_gui_checks WHERE state = 'queued'
                ORDER BY requested_at, id LIMIT 1 FOR UPDATE SKIP LOCKED
            )
            UPDATE platnik_gui_checks j
            SET state = 'running', started_at = clock_timestamp(), lease_id = gen_random_uuid(),
                lease_until = clock_timestamp() + interval '120 seconds', attempts = j.attempts + 1
            FROM next WHERE j.id = next.id
            RETURNING j.id, j.lease_id, j.nip, j.period;
        END
        $$;
    """).format(sql.Identifier(schema)).as_string())
    conn.execute(sql.SQL("""
        CREATE OR REPLACE FUNCTION platnik_gui_finish(job uuid, lease uuid, result jsonb, error text DEFAULT NULL)
        RETURNS boolean
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path TO pg_catalog, {}, pg_temp
        AS $$
        BEGIN
            IF result IS NULL OR pg_column_size(result) > 65536 OR jsonb_typeof(result) <> 'array' THEN
                RETURN false;
            END IF;
            IF jsonb_array_length(result) > 100 OR (error IS NOT NULL AND error NOT IN (
                'DesktopUnavailable', 'AppUnavailable', 'ScreenUnavailable', 'ProfileMismatch',
                'DuplicateEvidence', 'ScanLimit', 'Timeout', 'ReadFailed'
            )) THEN
                RETURN false;
            END IF;
            UPDATE platnik_gui_checks
            SET state = CASE
                    WHEN error IS NULL THEN 'completed'
                    WHEN error = 'DesktopUnavailable' THEN 'queued'
                    ELSE 'failed'
                END,
                findings = CASE WHEN error IS NULL THEN result ELSE '[]'::jsonb END,
                error_code = CASE WHEN error <> 'DesktopUnavailable' THEN error END,
                finished_at = CASE WHEN error IS DISTINCT FROM 'DesktopUnavailable' THEN clock_timestamp() END,
                started_at = CASE WHEN error <> 'DesktopUnavailable' OR error IS NULL THEN started_at END,
                attempts = CASE WHEN error = 'DesktopUnavailable' THEN GREATEST(attempts - 1, 0) ELSE attempts END,
                lease_id = NULL, lease_until = NULL
            WHERE id = job AND lease_id = lease AND state = 'running' AND lease_until > clock_timestamp();
            RETURN FOUND;
        END
        $$;
    """).format(sql.Identifier(schema)).as_string())
    conn.execute("REVOKE ALL ON FUNCTION platnik_gui_claim() FROM PUBLIC")
    conn.execute("REVOKE ALL ON FUNCTION platnik_gui_finish(uuid, uuid, jsonb, text) FROM PUBLIC")
    conn.execute("""
        INSERT INTO app_settings(klucz, wartosc) VALUES ('schema_hash', %s)
        ON CONFLICT(klucz) DO UPDATE SET wartosc=EXCLUDED.wartosc
    """, (VERSION,))
