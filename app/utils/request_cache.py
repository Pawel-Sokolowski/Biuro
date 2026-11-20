"""Small request-local caches; never retain permissions/settings across jobs."""
from contextvars import ContextVar
from contextlib import contextmanager
from functools import wraps
import threading
import time

_cache = ContextVar('request_cache', default=None)
_resources = ContextVar('request_resources', default=None)

# Uprawnienia pracownika zmieniają się rzadko, a każde żądanie ekranu pyta o nie
# ponownie (check + allowed_tabs + is_administrator). Krótka pamięć procesu
# oszczędza kilka zapytań na operację, a administrator unieważnia ją po zmianie ról.
_session_cache = {}
_session_lock = threading.RLock()
SESSION_TTL = 120.0


def session_value(key, load, ttl=SESSION_TTL):
    now = time.monotonic()
    with _session_lock:
        item = _session_cache.get(key)
        if item is not None and now - item[0] < ttl:
            return item[1]
    value = load()
    with _session_lock:
        _session_cache[key] = (now, value)
    return value


def clear_session_cache():
    with _session_lock:
        _session_cache.clear()


@contextmanager
def request_scope():
    token = _cache.set({})
    resources = []
    resource_token = _resources.set(resources)
    try:
        yield
    finally:
        try:
            for resource in reversed(resources):
                resource.close()
        finally:
            _resources.reset(resource_token)
            _cache.reset(token)


def own_resource(resource):
    resources = _resources.get()
    if resources is not None:
        resources.append(resource)
    return resource


def cached_value(key, load):
    cache = _cache.get()
    if cache is None:
        return load()
    if key not in cache:
        cache[key] = load()
    return cache[key]


def clear_request_cache():
    cache = _cache.get()
    if cache is not None:
        cache.clear()


def invalidate_permissions():
    """Po zmianie ról/pracowników pamięć uprawnień musi się od razu wyczyścić."""
    clear_session_cache()
    clear_request_cache()


def cached(function):
    @wraps(function)
    def call(*args, **kwargs):
        key = (function.__module__, function.__qualname__, args, tuple(sorted(kwargs.items())))
        return cached_value(key, lambda: function(*args, **kwargs))
    return call


def scoped(function):
    @wraps(function)
    def call(*args, **kwargs):
        with request_scope():
            return function(*args, **kwargs)
    return call


def prime_cached(function, value, *args):
    """Populate a cached DAO result from the same operation's bulk query."""
    cache = _cache.get()
    if cache is not None:
        owner = getattr(function, '__self__', None)
        arguments = (owner, *args) if owner is not None else args
        key = (function.__module__, function.__qualname__, arguments, ())
        cache[key] = value
