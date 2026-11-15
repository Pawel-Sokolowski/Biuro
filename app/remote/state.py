"""Explicit desktop/service separation, including in background threads."""
from contextlib import contextmanager
from contextvars import ContextVar
import threading

_service = ContextVar('office_service', default=False)
_actor = ContextVar('office_actor', default=None)
_client = None
_lock = threading.RLock()


def client():
    return None if _service.get() else _client


def configure(value):
    global _client
    with _lock:
        _client = value


def actor():
    return _actor.get()


@contextmanager
def service_scope(employee_id=None):
    service_token = _service.set(True)
    actor_token = _actor.set(employee_id)
    from app.utils.request_cache import request_scope
    try:
        with request_scope():
            yield
    finally:
        _actor.reset(actor_token)
        _service.reset(service_token)
