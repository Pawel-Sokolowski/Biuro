"""Versioned calendar operations and IMAP connection revisions."""
revision = "20261104_02"
down_revision = "20261104_01"
branch_labels = None
depends_on = None


def upgrade():
    from alembic import op
    from app.db.sync_schema import apply

    class Connection:
        def execute(self, statement):
            return op.get_bind().exec_driver_sql(statement)

    apply(Connection())


def downgrade():
    raise RuntimeError("Sync outbox contains pending changes. Restore a verified backup to downgrade.")
