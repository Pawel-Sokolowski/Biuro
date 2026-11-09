"""Consolidate duplicate private chat histories and enforce a single channel."""
revision = '20261108_01'
down_revision = '20261106_01'
branch_labels = None
depends_on = None


def upgrade():
    from alembic import op
    from app.db.chat_integrity_schema import apply

    class Connection:
        def execute(self, statement):
            return op.get_bind().exec_driver_sql(statement)

    apply(Connection())


def downgrade():
    from alembic import op
    op.get_bind().exec_driver_sql('DROP INDEX IF EXISTS uq_chat_private_name')
