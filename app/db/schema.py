"""Schema compatibility API and versioned upgrade entry point.

schema_baseline is the frozen pre-Alembic bootstrap/repair API. Add future DDL
as Alembic revisions rather than editing/replaying that historical procedure.
"""
from pathlib import Path
from app.db.schema_baseline import init_db as _baseline_init_db
from app.db.dao_conn import get_conn

SCHEMA_VERSION = "2026-11-05.2"


def init_db():
    """Explicit bootstrap/repair API; normal startup uses Alembic upgrades."""
    _baseline_init_db()
    from app.db.sync_schema import apply
    with get_conn() as conn:
        apply(conn)
        from app.db.address_book_schema import apply as apply_address_book
        apply_address_book(conn)
        from app.db.payment_reconciliation_schema import apply as apply_payment_reconciliation
        apply_payment_reconciliation(conn)


def schema_is_current():
    with get_conn() as conn:
        if not conn.execute("SELECT to_regclass('app_settings') IS NOT NULL").fetchone()[0]:
            return False
        row = conn.execute("SELECT wartosc FROM app_settings WHERE klucz='schema_hash'").fetchone()
        return bool(row and row[0] == SCHEMA_VERSION)


def upgrade_database():
    from alembic import command
    from alembic.config import Config
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    command.upgrade(config, "head")
