"""Indexed storage for company report history and worker read requests."""

VERSION = '2026-11-21.2'


def apply(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS company_checking_snapshots (
            source TEXT NOT NULL,
            company_id INTEGER NOT NULL REFERENCES firmy(id) ON DELETE CASCADE,
            period TEXT NOT NULL,
            payload JSONB NOT NULL,
            PRIMARY KEY (source, company_id, period),
            CHECK (period = '' OR period ~ '^[0-9]{4}(-(0[1-9]|1[0-2]))?$')
        )
    """)
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_checking_snapshots_company
                    ON company_checking_snapshots(company_id)""")
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_checking_history_period
                    ON company_checking_snapshots(period, company_id) WHERE source='enova_checking'""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS checking_requests (
            id TEXT PRIMARY KEY,
            actor_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            company_ids INTEGER[] NOT NULL,
            period TEXT NOT NULL,
            state TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            finished_at TIMESTAMPTZ,
            payload JSONB NOT NULL
        )
    """)
    # Older bootstrap versions could create an unused prototype of this table.
    # Preserve any records written to it and maintain the actual query columns.
    conn.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_schema=current_schema() AND table_name='checking_requests'
                             AND column_name='payload') THEN
                ALTER TABLE checking_requests ADD COLUMN payload JSONB;
                UPDATE checking_requests SET payload = jsonb_strip_nulls(jsonb_build_object(
                    'id', id, 'actor_id', actor_id, 'kind', kind, 'company_ids', company_ids::jsonb,
                    'period', CASE WHEN period ~ '^[0-9]{4}$' THEN to_jsonb(period::integer) ELSE to_jsonb(period) END,
                    'state', state, 'attempts', attempts, 'created_at', created_at, 'started_at', started_at,
                    'finished_at', finished_at, 'error', error, 'results', results::jsonb));
                ALTER TABLE checking_requests ALTER COLUMN payload SET NOT NULL,
                    ALTER COLUMN company_ids TYPE INTEGER[] USING translate(company_ids,'[]','{}')::integer[],
                    ALTER COLUMN created_at TYPE TIMESTAMPTZ USING created_at::timestamptz,
                    ALTER COLUMN finished_at TYPE TIMESTAMPTZ USING finished_at::timestamptz;
            END IF;
        END $$
    """)
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_checking_requests_state
                    ON checking_requests(state, created_at)""")
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_checking_requests_actor
                    ON checking_requests(actor_id, created_at DESC)""")
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_checking_requests_finished
                    ON checking_requests(finished_at) WHERE state IN ('completed','partial','failed')""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS enova_stage_requests (
            company_id INTEGER NOT NULL REFERENCES firmy(id) ON DELETE CASCADE,
            period TEXT NOT NULL CHECK (period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'),
            revision TEXT NOT NULL,
            PRIMARY KEY(company_id, period)
        )
    """)


def mark_current(conn):
    conn.execute(f"""INSERT INTO app_settings(klucz, wartosc) VALUES ('schema_hash', '{VERSION}')
                    ON CONFLICT(klucz) DO UPDATE SET wartosc=EXCLUDED.wartosc""")


def migrate_legacy(conn):
    # The DELETE uses RETURNING from each insert. Unrecognized, orphaned or
    # conflicting values survive in their original location.
    for pattern, period in (
        (r'^(enova_checking|enova_snapshot|enova_jpk|platnik_declarations)_([0-9]{1,10})_([0-9]{4}-(?:0[1-9]|1[0-2]))$', 'parts[3]'),
        (r'^(platnik_declarations)_([0-9]{1,10})_year_([0-9]{4})$', 'parts[3]'),
        (r'^(checking_obligations|enova_hr|ezla_sync)_([0-9]{1,10})$', "''"),
    ):
        conn.execute(f"""
            WITH legacy AS (
                SELECT a.klucz, parts[1] AS source, f.id AS company_id, {period} AS period,
                       CASE WHEN parts[1]='platnik_declarations' THEN to_jsonb(coalesce(a.wartosc,''))
                            ELSE coalesce(nullif(a.wartosc,''),'{{}}')::jsonb END AS payload
                FROM app_settings a CROSS JOIN LATERAL regexp_match(a.klucz, '{pattern}') AS m(parts)
                JOIN firmy f ON f.id=parts[2]::bigint
            ), moved AS (
                INSERT INTO company_checking_snapshots(source,company_id,period,payload)
                SELECT source,company_id,period,payload FROM legacy
                ON CONFLICT DO NOTHING RETURNING source,company_id,period,payload
            )
            DELETE FROM app_settings a USING legacy,moved
            WHERE a.klucz=legacy.klucz
              AND (legacy.source,legacy.company_id,legacy.period,legacy.payload)
                = (moved.source,moved.company_id,moved.period,moved.payload)
        """)
    conn.execute("""
        WITH legacy AS (
            SELECT klucz, a.wartosc::jsonb AS payload
            FROM app_settings a WHERE klucz ~ '^checking_refresh_[0-9a-f]{32}$'
        ), moved AS (
            INSERT INTO checking_requests(id,actor_id,kind,company_ids,period,state,created_at,finished_at,payload)
            SELECT payload->>'id', (payload->>'actor_id')::int, payload->>'kind',
                   ARRAY(SELECT jsonb_array_elements_text(payload->'company_ids')::int), payload->>'period',
                   payload->>'state', (payload->>'created_at')::timestamptz,
                   (payload->>'finished_at')::timestamptz, payload
            FROM legacy
            ON CONFLICT DO NOTHING RETURNING id,payload
        )
        DELETE FROM app_settings a USING legacy,moved
        WHERE a.klucz=legacy.klucz AND legacy.payload=moved.payload
    """)
    conn.execute("""
        WITH legacy AS (
            SELECT a.klucz, f.id AS company_id, parts[2] AS period, a.wartosc::jsonb->>'revision' AS revision
            FROM app_settings a CROSS JOIN LATERAL
                regexp_match(a.klucz, '^enova_stage_pending_([0-9]{1,10})_([0-9]{4}-(?:0[1-9]|1[0-2]))$') AS m(parts)
            JOIN firmy f ON f.id=parts[1]::bigint
        ), moved AS (
            INSERT INTO enova_stage_requests(company_id,period,revision)
            SELECT company_id,period,revision FROM legacy
            ON CONFLICT DO NOTHING RETURNING company_id,period,revision
        )
        DELETE FROM app_settings a USING legacy,moved
        WHERE a.klucz=legacy.klucz AND (legacy.company_id,legacy.period,legacy.revision)
            = (moved.company_id,moved.period,moved.revision)
    """)
