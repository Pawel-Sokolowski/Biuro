"""Move checking history and worker requests to indexed tables."""

revision = '20261121_02'
down_revision = '20261121_01'
branch_labels = None
depends_on = None


def upgrade():
    from alembic import op
    from app.db.checking_schema import apply, mark_current, migrate_legacy

    class Connection:
        def execute(self, statement):
            return op.get_bind().exec_driver_sql(statement)

    conn = Connection()
    apply(conn)
    migrate_legacy(conn)
    mark_current(conn)


def downgrade():
    raise RuntimeError('Use a verified backup to restore the previous schema.')
