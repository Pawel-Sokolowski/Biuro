"""Durable Saldeo notification inbox."""
revision = '20261113_01'
down_revision = '20261109_02'
branch_labels = None
depends_on = None


def upgrade():
    from alembic import op
    from app.db.saldeo_web_schema import apply
    class Connection:
        def execute(self, statement):
            return op.get_bind().exec_driver_sql(statement)
    apply(Connection())


def downgrade():
    raise RuntimeError('Restore a verified backup rather than discard notification history.')
