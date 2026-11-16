"""Remove the legacy billing-method label without changing tax or price data."""
revision = '20261116_01'
down_revision = '20261113_01'
branch_labels = None
depends_on = None


def upgrade():
    from alembic import op
    from app.db.billing_label_schema import apply
    class Connection:
        def execute(self, statement):
            return op.get_bind().exec_driver_sql(statement)
    apply(Connection())


def downgrade():
    raise RuntimeError('Restore a verified backup to restore removed legacy billing labels.')
