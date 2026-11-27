"""Keep sent messages across IMAP outages."""

revision = '20261127_01'
down_revision = '20261121_08'
branch_labels = None
depends_on = None


def upgrade():
    from alembic import op
    from app.db.sent_copy_schema import apply

    class Connection:
        def execute(self, statement, params=()):
            return op.get_bind().exec_driver_sql(statement, params)

    apply(Connection())


def downgrade():
    raise RuntimeError('Use a verified backup to restore the previous schema.')
