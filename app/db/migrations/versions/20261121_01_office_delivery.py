"""Create bulk mail history and add retry scheduling for invoice PDF delivery."""

revision = '20261121_01'
down_revision = '20261116_01'
branch_labels = None
depends_on = None


def upgrade():
    from alembic import op
    from app.db.office_delivery_schema import apply

    class Connection:
        def execute(self, statement):
            return op.get_bind().exec_driver_sql(statement)

    apply(Connection())


def downgrade():
    raise RuntimeError('Use a verified backup to restore the previous schema.')
