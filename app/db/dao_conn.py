# app/db/dao_conn.py
"""Połączenie z PostgreSQL (psycopg 3)."""
from __future__ import annotations

import atexit
import logging
import os
import threading
from contextlib import contextmanager
from time import perf_counter

import psycopg
from psycopg_pool import ConnectionPool

from app.config import get_dsn, get_schema
from app.remote.boundary import endpoint as office_endpoint


class Row(tuple):
    """Wiersz wyniku: dostęp po indeksie (row[0]), po nazwie (row["id"]), keys() i dict(row)."""

    def __new__(cls, names, values, indexes=None):
        obj = super().__new__(cls, values)
        obj._names = names
        obj._indexes = (
            indexes
            if indexes is not None
            else {
                name: i
                for i, name in reversed(list(enumerate(names)))
            }
        )
        return obj

    def __getitem__(self, key):
        if isinstance(key, str):
            try:
                return tuple.__getitem__(
                    self,
                    self._indexes[key],
                )
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
    names = [
        c.name
        for c in (cursor.description or [])
    ]
    indexes = {
        name: i
        for i, name in reversed(list(enumerate(names)))
    }

    return lambda values: Row(
        names,
        values,
        indexes,
    )


# Keep one connection warm so the first UI/worker query after an idle period does
# not pay for DNS, TCP/TLS and PostgreSQL authentication.  Values remain bounded
# and can be tuned per installation without a code change.
def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


# Pula trzyma gotowe połączenia: po włączeniu TLS każde nowe połączenie kosztuje
# uzgodnienie szyfrowania, więc lista nie może czekać, aż pula je otworzy.
# Serwer biura wykonuje do 16 operacji naraz (BIO_API_WORKERS), więc pula musi
# mieć co najmniej tyle gotowych połączeń.
_POOL_MIN = _env_int("BIO_DB_POOL_MIN", 8, 0, 16)
_POOL_MAX = _env_int("BIO_DB_POOL_MAX", 16, 1, 32)
_POOL_IDLE = _env_int("BIO_DB_POOL_IDLE", 300, 30, 3600)
_POOL_TIMEOUT = _env_int("BIO_DB_POOL_TIMEOUT", 10, 1, 60)

# Never allow min_size to exceed max_size if an administrator supplied
# inconsistent environment values.
_POOL_MIN = min(_POOL_MIN, _POOL_MAX)

_pool: dict[
    tuple[str, str | None],
    ConnectionPool,
] = {}

_coordination_pool: dict[
    tuple[str, str | None],
    ConnectionPool,
] = {}

_activity_pool: dict[
    tuple[str, str | None],
    ConnectionPool,
] = {}

_pool_lock = threading.Lock()


class PooledConnection:
    """
    Cienka nakładka:
    close() zwraca połączenie do puli zamiast je zamykać.
    """

    def __init__(
        self,
        conn: psycopg.Connection,
        pool: ConnectionPool,
    ):
        self._conn = conn
        self._pool = pool

    def __getattr__(self, name):
        return getattr(
            self._conn,
            name,
        )

    def __enter__(self):
        return self

    def __exit__(
        self,
        exc_type,
        exc,
        tb,
    ):
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
        # Legacy DAO error paths must not leave
        # a checked-out connection behind.
        try:
            self.close()
        except Exception:
            pass


def close_pool(
    key: str | None = None,
) -> None:
    """
    Zamyka bezczynne połączenia z puli
    dla danego schematu.
    """

    with _pool_lock:
        pools = [
            mapping.pop(k)
            for mapping in (
                _pool,
                _coordination_pool,
                _activity_pool,
            )
            for k in list(mapping)
            if k[1] == key
        ]

    for pool in pools:
        pool.close()


def close_all_pools() -> None:
    with _pool_lock:
        pools = [
            pool
            for mapping in (
                _pool,
                _coordination_pool,
                _activity_pool,
            )
            for pool in mapping.values()
        ]

        _pool.clear()
        _coordination_pool.clear()
        _activity_pool.clear()

    for pool in pools:
        pool.close()


def get_conn() -> PooledConnection:
    return _get_connection(
        _pool,
        _POOL_MAX,
    )


def get_activity_conn() -> PooledConnection:
    """
    Progress writes must not wait for data slots
    held by the jobs they observe.
    """

    return _get_connection(
        _activity_pool,
        2,
    )


def get_coordination_conn() -> PooledConnection:
    """
    Bounded account-lock connections cannot
    occupy the eight data slots.
    """

    return _get_connection(
        _coordination_pool,
        4,
    )


@contextmanager
def advisory_lock(lock_id: int):
    """
    Utrzymuje sesyjną blokadę PostgreSQL bez pozostawiania
    wielominutowej transakcji otwartej.

    Yield:
        True  - blokada została zdobyta.
        False - blokada jest już zajęta.
    """

    lock = get_coordination_conn()
    acquired = False

    try:
        row = lock.execute(
            'SELECT pg_try_advisory_lock(%s)',
            (lock_id,),
        ).fetchone()

        acquired = bool(row[0])

        # pg_try_advisory_lock() jest blokadą sesyjną.
        # Kończymy więc transakcję natychmiast, ale lock
        # nadal należy do tej sesji PostgreSQL.
        lock.commit()

        yield acquired

    finally:
        if acquired:
            try:
                lock.execute(
                    'SELECT pg_advisory_unlock(%s)',
                    (lock_id,),
                )

                lock.commit()

            except Exception:
                # Nie zwracamy celowo potencjalnie wadliwej
                # sesji z advisory lock do normalnej pracy.
                try:
                    lock.rollback()
                except Exception:
                    pass

                underlying = getattr(
                    lock,
                    '_conn',
                    None,
                )

                if underlying is not None:
                    try:
                        underlying.close()
                    except Exception:
                        pass

                raise

        lock.close()


@office_endpoint(
    'app.db.dao_conn.database_context'
)
def database_context() -> str:
    """
    Actual PostgreSQL endpoint/database/schema,
    without credentials.
    """

    with get_conn() as conn:
        row = conn.execute(
            '''
            SELECT
                current_database() AS database_name,
                (
                    SELECT n.nspname
                    FROM pg_class c
                    JOIN pg_namespace n
                        ON n.oid = c.relnamespace
                    WHERE c.oid = to_regclass('app_settings')
                ) AS schema_name,
                inet_server_addr()::text AS server_address,
                inet_server_port() AS server_port
            '''
        ).fetchone()

    address = (
        row['server_address']
        or 'lokalne połączenie'
    )

    if ':' in address:
        address = '[' + address + ']'

    return (
        f"{address}:"
        f"{row['server_port'] or '—'}/"
        f"{row['database_name']} "
        f"· schemat: {row['schema_name'] or '—'}"
    )


def _get_connection(
    mapping,
    max_size,
) -> PooledConnection:
    from app.remote.state import client

    if client() is not None:
        raise PermissionError(
            'Komputer pracownika korzysta wyłącznie '
            'z usługi biura, bez bezpośredniego '
            'dostępu do bazy.'
        )

    schema = get_schema()
    dsn = get_dsn()
    key = (dsn, schema)

    with _pool_lock:
        pool = mapping.get(key)

        if pool is None:
            options = (
                f"-c search_path={schema},public"
                if schema
                else None
            )

            pool = ConnectionPool(
                dsn,
                min_size=min(_POOL_MIN, max_size),
                max_size=max_size,
                timeout=_POOL_TIMEOUT,
                max_idle=_POOL_IDLE,
                max_lifetime=1800,
                kwargs={
                    "row_factory": _row_factory,
                    "options": options,
                    "connect_timeout": _POOL_TIMEOUT,
                    "application_name": "biuro",
                },
                check=ConnectionPool.check_connection,
                open=True,
            )

            mapping[key] = pool

    started = perf_counter()

    conn = pool.getconn()

    elapsed = perf_counter() - started

    if elapsed >= 0.1:
        logging.getLogger(__name__).info(
            'database_pool_wait duration=%.3f stats=%s',
            elapsed,
            pool.get_stats(),
        )

    return PooledConnection(
        conn,
        pool,
    )


atexit.register(close_all_pools)