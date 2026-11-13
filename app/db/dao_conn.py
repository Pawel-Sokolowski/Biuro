# app/db/dao_conn.py
"""Połączenie z PostgreSQL (psycopg 3)."""
from __future__ import annotations

import threading
import atexit
import logging
from time import perf_counter

import psycopg
from psycopg_pool import ConnectionPool

from app.config import get_dsn, get_schema


class Row(tuple):
    """Wiersz wyniku: dostęp po indeksie (row[0]), po nazwie (row["id"]), keys() i dict(row)."""

    def __new__(cls, names, values, indexes=None):
        obj = super().__new__(cls, values)
        obj._names = names
        obj._indexes = indexes if indexes is not None else {name: i for i, name in reversed(list(enumerate(names)))}
        return obj

    def __getitem__(self, key):
        if isinstance(key, str):
            try:
                return tuple.__getitem__(self, self._indexes[key])
            except KeyError:
                raise KeyError(key) from None
        return tuple.__getitem__(self, key)

    def keys(self):
        return list(self._names)

    def get(self, key, default=None):
        try:
            return self[key]
        except (KeyError, IndexError):
            return default


def _row_factory(cursor):
    names = [c.name for c in (cursor.description or [])]
    indexes = {name: i for i, name in reversed(list(enumerate(names)))}
    return lambda values: Row(names, values, indexes)


_POOL_MAX = 8
_pool: dict[tuple[str, str | None], ConnectionPool] = {}
_coordination_pool: dict[tuple[str, str | None], ConnectionPool] = {}
_pool_lock = threading.Lock()


class PooledConnection:
    """Cienka nak?adka: close() zwraca po??czenie do puli zamiast je zamyka? (nawi?zanie ~150 ms przez sie?)."""

    def __init__(self, conn: psycopg.Connection, pool: ConnectionPool):
        self._conn = conn
        self._pool = pool

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        finally:
            self.close()

    def close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is None:
            return
        try:
            if not conn.closed:
                conn.rollback()
        except psycopg.Error:
            conn.close()
        finally:
            self._pool.putconn(conn)

    def __del__(self):
        # Legacy DAO error paths must not leave a checked-out connection behind.
        try:
            self.close()
        except Exception:
            pass


def close_pool(key: str | None = None) -> None:
    """Zamyka bezczynne po??czenia z puli dla danego schematu (u?ywane m.in. w testach)."""
    with _pool_lock:
        pools = [mapping.pop(k) for mapping in (_pool, _coordination_pool) for k in list(mapping) if k[1] == key]
    for pool in pools:
        pool.close()


def close_all_pools() -> None:
    with _pool_lock:
        pools = [pool for mapping in (_pool, _coordination_pool) for pool in mapping.values()]
        _pool.clear()
        _coordination_pool.clear()
    for pool in pools:
        pool.close()


def get_conn() -> PooledConnection:
    return _get_connection(_pool, _POOL_MAX)


def get_coordination_conn() -> PooledConnection:
    """Bounded account-lock connections cannot occupy the eight data slots."""
    return _get_connection(_coordination_pool, 4)


def database_context() -> str:
    """Actual PostgreSQL endpoint/database/schema, without credentials."""
    with get_conn() as conn:
        row = conn.execute('''SELECT current_database() AS database_name,
            (SELECT n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE c.oid=to_regclass('app_settings')) AS schema_name,
            inet_server_addr()::text AS server_address,inet_server_port() AS server_port''').fetchone()
    address = row['server_address'] or 'lokalne połączenie'
    if ':' in address:
        address = '['+address+']'
    return f"{address}:{row['server_port'] or '—'}/{row['database_name']} · schemat: {row['schema_name'] or '—'}"


def _get_connection(mapping, max_size) -> PooledConnection:
    schema = get_schema()
    dsn = get_dsn()
    key = (dsn, schema)
    with _pool_lock:
        pool = mapping.get(key)
        if pool is None:
            options = f"-c search_path={schema},public" if schema else None
            pool = ConnectionPool(
                dsn, min_size=0, max_size=max_size, timeout=10,
                max_idle=60, max_lifetime=1800,
                kwargs={"row_factory": _row_factory, "options": options, "connect_timeout": 10},
                check=ConnectionPool.check_connection, open=True,
            )
            mapping[key] = pool
    started = perf_counter()
    conn = pool.getconn()
    elapsed = perf_counter() - started
    if elapsed >= 0.1:
        logging.getLogger(__name__).info('database_pool_wait duration=%.3f stats=%s', elapsed, pool.get_stats())
    return PooledConnection(conn, pool)


atexit.register(close_all_pools)
