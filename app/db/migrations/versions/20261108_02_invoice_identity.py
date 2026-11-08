"""Retain the seller printed on an issued invoice."""
revision = '20261108_02'
down_revision = '20261108_01'
branch_labels = None
depends_on = None


def upgrade():
    from alembic import op
    from app.db.invoice_identity_schema import apply

    class Connection:
        def execute(self, statement):
            return op.get_bind().exec_driver_sql(statement)

    apply(Connection())


def downgrade():
    raise RuntimeError('Restore a backup rather than discard issued invoice seller snapshots.')
