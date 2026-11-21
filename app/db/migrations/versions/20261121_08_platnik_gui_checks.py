"""Queue for the interactive Płatnik status agent."""

revision = '20261121_08'
down_revision = '20261121_07'
branch_labels = None
depends_on = None


def upgrade():
    from alembic import op
    from app.db.platnik_gui_schema import apply

    class Connection:
        def execute(self, statement, params=()):
            return op.get_bind().exec_driver_sql(statement, params)

    apply(Connection())


def downgrade():
    raise RuntimeError('Use a verified backup to restore the previous schema.')
