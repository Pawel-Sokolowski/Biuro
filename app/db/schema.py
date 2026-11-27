"""Schema compatibility API and versioned upgrade entry point.

schema_baseline is the frozen pre-Alembic bootstrap/repair API. Add future DDL
as Alembic revisions rather than editing/replaying that historical procedure.
"""
from app.remote.boundary import endpoint as office_endpoint
from pathlib import Path
from app.db.schema_baseline import init_db as _baseline_init_db
from app.db.dao_conn import get_conn
from app.db.sent_copy_schema import VERSION as SCHEMA_VERSION


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
        from app.db.reliability_schema import apply as apply_reliability
        apply_reliability(conn)
        from app.db.chat_integrity_schema import apply as apply_chat_integrity
        apply_chat_integrity(conn)
        from app.db.invoice_identity_schema import apply as apply_invoice_identity
        apply_invoice_identity(conn)
        from app.db.reminder_schema import apply as apply_reminders
        apply_reminders(conn)
        from app.db.monthly_counts_schema import apply as apply_monthly_counts
        apply_monthly_counts(conn)
        from app.db.saldeo_web_schema import apply as apply_saldeo_web
        apply_saldeo_web(conn)
        from app.db.billing_label_schema import apply as remove_billing_label
        remove_billing_label(conn)
        from app.db.office_delivery_schema import apply as apply_office_delivery
        apply_office_delivery(conn)
        from app.db.checking_schema import apply as apply_checking, migrate_legacy
        apply_checking(conn)
        migrate_legacy(conn)
        from app.db.query_index_schema import apply as apply_query_indexes
        apply_query_indexes(conn)
        from app.db.reminder_index_schema import apply as apply_reminder_indexes
        apply_reminder_indexes(conn)
        from app.db.review_fixes_schema import apply as apply_review_fixes
        apply_review_fixes(conn)
        from app.db.platnik_links_schema import apply as apply_platnik_links
        apply_platnik_links(conn)
        from app.db.company_bank_account_schema import apply as apply_company_bank_accounts
        apply_company_bank_accounts(conn)
        from app.db.platnik_gui_schema import apply as apply_platnik_gui_checks
        apply_platnik_gui_checks(conn)
        from app.db.sent_copy_schema import apply as apply_sent_copies
        apply_sent_copies(conn)


@office_endpoint('app.db.schema.schema_is_current')
def schema_is_current():
    with get_conn() as conn:
        if not conn.execute("SELECT to_regclass('app_settings') IS NOT NULL").fetchone()[0]:
            return False
        row = conn.execute("SELECT wartosc FROM app_settings WHERE klucz='schema_hash'").fetchone()
        return bool(row and row[0] == SCHEMA_VERSION)


def upgrade_database():
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    heads = set(ScriptDirectory.from_config(config).get_heads())
    # Some local checkouts retain the old service-tables branch. The release
    # branch already creates those tables in revisions 20261121_01 and _02.
    target = "20261127_01" if heads == {"20261116_03", "20261127_01"} else "head"
    command.upgrade(config, target)
