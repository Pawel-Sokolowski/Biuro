"""Short-lived navigation measurements; no arguments or business data."""
from contextlib import contextmanager
from contextvars import ContextVar
import logging
from time import perf_counter

_activity = ContextVar('office_navigation_timing',default=None)


def record_request(route,operation,duration):
    activity = _activity.get()
    if activity is not None:
        key = operation if operation != '-' else route
        item = activity.setdefault(key,[0,0.0])
        item[0] += 1
        item[1] += duration


@contextmanager
def navigation_timing(index):
    activity = {}
    token = _activity.set(activity)
    started = perf_counter()
    try:
        yield
    finally:
        _activity.reset(token)
        duration = perf_counter()-started
        if duration >= .25:
            slowest = sorted(activity.items(),key=lambda item:item[1][1],reverse=True)[:5]
            logging.getLogger(__name__).info(
                'office_navigation_slow page=%s duration=%.3f requests=%s network_seconds=%.3f top=%s',
                index,duration,sum(item[0] for item in activity.values()),
                sum(item[1] for item in activity.values()),
                [(name,count,round(seconds,3)) for name,(count,seconds) in slowest])
