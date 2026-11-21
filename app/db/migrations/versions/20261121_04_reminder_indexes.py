"""Index the recipient and active invoice trash lookups."""

revision = '20261121_04'
down_revision = '20261121_03'
branch_labels = None
depends_on = None


def upgrade():
    from alembic import op
    from app.db.reminder_index_schema import apply

    class Connection:
        def execute(self, statement, params=()):
            return op.get_bind().exec_driver_sql(statement, params)

    apply(Connection())


def downgrade():
    raise RuntimeError('Use a verified backup to restore the previous schema.')
