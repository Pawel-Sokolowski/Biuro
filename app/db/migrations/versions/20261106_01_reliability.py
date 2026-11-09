"""Detect stale drafts and retain timer sessions after abnormal shutdown."""
revision = '20261106_01'
down_revision = '20261105_02'
branch_labels = None
depends_on = None


def upgrade():
    from alembic import op
    from app.db.reliability_schema import apply

    class Connection:
        def execute(self, statement):
            return op.get_bind().exec_driver_sql(statement)

    apply(Connection())


def downgrade():
    raise RuntimeError('Recover or stop active timers before restoring an older database backup.')
