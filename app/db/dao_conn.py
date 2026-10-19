# app/db/dao_conn.py
"""Połączenie z PostgreSQL (psycopg 3)."""
from __future__ import annotations

import threading

import psycopg
import psycopg.pq

from app.config import get_dsn, get_schema


class Row(tuple):
    """Wiersz wyniku: dostęp po indeksie (row[0]), po nazwie (row["id"]), keys() i dict(row)."""

    def __new__(cls, names, values):
        obj = super().__new__(cls, values)
        obj._names = names
        return obj

    def __getitem__(self, key):
        if isinstance(key, str):
            try:
                return tuple.__getitem__(self, self._names.index(key))
            except ValueError:
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
    return lambda values: Row(names, values)


_POOL_MAX = 4
_pool: dict[str | None, list] = {}
_pool_lock = threading.Lock()


class PooledConnection:
    """Cienka nak?adka: close() zwraca po??czenie do puli zamiast je zamyka? (nawi?zanie ~150 ms przez sie?)."""

    def __init__(self, conn: psycopg.Connection, key: str | None):
        self._conn = conn
        self._key = key

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
        if conn is None or conn.closed:
            return
        try:
            conn.rollback()
        except psycopg.Error:
            conn.close()
            return
        with _pool_lock:
            bucket = _pool.setdefault(self._key, [])
            if len(bucket) < _POOL_MAX:
                bucket.append(conn)
                return
        conn.close()


def close_pool(key: str | None = None) -> None:
    """Zamyka bezczynne po??czenia z puli dla danego schematu (u?ywane m.in. w testach)."""
    with _pool_lock:
        conns = _pool.pop(key, [])
    for conn in conns:
        try:
            conn.close()
        except psycopg.Error:
            pass


def get_conn() -> PooledConnection:
    schema = get_schema()
    with _pool_lock:
        bucket = _pool.setdefault(schema, [])
        while bucket:
            conn = bucket.pop()
            if not conn.closed and not conn.info.transaction_status == psycopg.pq.TransactionStatus.UNKNOWN:
                return PooledConnection(conn, schema)
    options = f"-c search_path={schema},public" if schema else None
    conn = psycopg.connect(get_dsn(), row_factory=_row_factory, options=options, connect_timeout=10)
    return PooledConnection(conn, schema)
