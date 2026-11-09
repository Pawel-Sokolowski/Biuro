"""Durable document and payment reminder history."""
revision = '20261109_01'
down_revision = '20261108_02'
branch_labels = None
depends_on = None


def upgrade():
    from alembic import op
    from app.db.reminder_schema import apply

    class Connection:
        def execute(self, statement):
            return op.get_bind().exec_driver_sql(statement)

    apply(Connection())


def downgrade():
    raise RuntimeError('Reminder history prevents duplicate emails. Restore a verified backup to downgrade.')
