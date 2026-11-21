"""Index reconciliation, monthly invoice reads and normalized company NIPs."""

revision = '20261121_03'
down_revision = '20261121_02'
branch_labels = None
depends_on = None


def upgrade():
    from alembic import op
    from app.db.query_index_schema import apply

    class Connection:
        def execute(self, statement):
            return op.get_bind().exec_driver_sql(statement)

    apply(Connection())


def downgrade():
    raise RuntimeError('Use a verified backup to restore the previous schema.')
