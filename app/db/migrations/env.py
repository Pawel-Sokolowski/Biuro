"""Alembic uses the selected schema and serializes concurrent upgrade commands."""
from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool
from app.config import get_dsn, get_schema

schema = get_schema()
kwargs = {"connect_timeout": 10}
if schema:
    kwargs["options"] = f"-c search_path={schema},public"
engine = create_engine(make_url(get_dsn()).set(drivername="postgresql+psycopg"),
                       poolclass=NullPool, connect_args=kwargs)
try:
    with engine.connect() as connection, connection.begin():
        connection.exec_driver_sql("SELECT pg_advisory_xact_lock(748202)")
        selected_schema = connection.exec_driver_sql("SELECT current_schema()").scalar_one()
        context.configure(connection=connection, version_table_schema=selected_schema)
        context.run_migrations()
finally:
    engine.dispose()
