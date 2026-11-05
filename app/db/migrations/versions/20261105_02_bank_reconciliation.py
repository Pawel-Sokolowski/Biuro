"""Link manually recorded bank payments to imported operations without duplication."""
revision = '20261105_02'
down_revision = '20261105_01'
branch_labels = None
depends_on = None


def upgrade():
    from alembic import op
    from app.db.payment_reconciliation_schema import apply

    class Connection:
        def execute(self, statement):
            return op.get_bind().exec_driver_sql(statement)

    apply(Connection())


def downgrade():
    raise RuntimeError('Manual payment links preserve settlement history. Restore a verified backup to downgrade.')
