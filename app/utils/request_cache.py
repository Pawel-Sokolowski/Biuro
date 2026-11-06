"""Small request-local caches; never retain permissions/settings across jobs."""
from contextvars import ContextVar
from contextlib import contextmanager
from functools import wraps

_cache = ContextVar('request_cache', default=None)
_resources = ContextVar('request_resources', default=None)


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
