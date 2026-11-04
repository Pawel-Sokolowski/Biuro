"""Baseline: preserve/upgrade legacy databases and add mailbox UID generations.

Revision ID: 20261104_01
"""
revision = "20261104_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    from app.db.schema_baseline import init_db
    init_db()


def downgrade():
    raise RuntimeError("The accounting baseline cannot be downgraded destructively. Restore a verified backup.")
