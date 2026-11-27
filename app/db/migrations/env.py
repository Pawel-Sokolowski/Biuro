"""Alembic uses the selected schema and serializes concurrent upgrade commands."""
from alembic import context
import psycopg
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool
from app.config import get_dsn, get_schema

schema = get_schema()
kwargs = {"connect_timeout": 10}
if schema:
    kwargs["options"] = f"-c search_path={schema},public"
dsn = get_dsn()
engine = create_engine("postgresql+psycopg://", poolclass=NullPool,
                       creator=lambda: psycopg.connect(dsn, **kwargs))
try:
    with engine.connect() as connection, connection.begin():
        connection.exec_driver_sql("SELECT pg_advisory_xact_lock(748202)")
        selected_schema = connection.exec_driver_sql("SELECT current_schema()").scalar_one()
        if schema and selected_schema != schema:
            raise RuntimeError(f"Nie można wykonać migracji w schemacie {schema}.")
        context.configure(connection=connection, version_table_schema=selected_schema)
        context.run_migrations()
finally:
    engine.dispose()
