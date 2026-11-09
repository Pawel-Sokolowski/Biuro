"""Authoritative monthly accounting counts imported from XLSX."""
revision = '20261109_02'
down_revision = '20261109_01'
branch_labels = None
depends_on = None


def upgrade():
    from alembic import op
    from app.db.monthly_counts_schema import apply

    class Connection:
        def execute(self, statement):
            return op.get_bind().exec_driver_sql(statement)

    apply(Connection())


def downgrade():
    raise RuntimeError('Restore a verified backup rather than discard document count import provenance.')
