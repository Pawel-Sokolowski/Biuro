"""Index bank reconciliation references and schedule Enova retries."""

revision = '20261121_05'
down_revision = '20261121_04'
branch_labels = None
depends_on = None


def upgrade():
    from alembic import op
    from app.db.review_fixes_schema import apply

    class Connection:
        def execute(self, statement, params=()):
            return op.get_bind().exec_driver_sql(statement, params)

    apply(Connection())


def downgrade():
    raise RuntimeError('Use a verified backup to restore the previous schema.')
