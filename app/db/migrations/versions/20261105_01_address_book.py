"""Shared global contacts independent of client companies."""
revision = '20261105_01'
down_revision = '20261104_02'
branch_labels = None
depends_on = None


def upgrade():
    from alembic import op
    from app.db.address_book_schema import apply

    class Connection:
        def execute(self, statement):
            return op.get_bind().exec_driver_sql(statement)

    apply(Connection())


def downgrade():
    raise RuntimeError('The shared address book contains user contacts. Restore a verified backup to downgrade.')
