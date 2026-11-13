"""Bounded Qt background jobs with stale-result rejection and schema isolation."""
from __future__ import annotations

from contextvars import copy_context
import threading
import logging
from time import perf_counter
from typing import Callable, TypeVar

Result = TypeVar("Result")
from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal
from app.config import get_schema, schema_context
from app.utils.request_cache import request_scope


_pool = QThreadPool()
_pool.setMaxThreadCount(4)
metadata_pool = QThreadPool()
metadata_pool.setMaxThreadCount(1)


class _Signals(QObject):
    finished = pyqtSignal(object)


class _Job(QRunnable):
    def __init__(self, generation, work, schema):
        super().__init__()
        self.setAutoDelete(False)
        self.generation, self.work, self.schema = generation, work, schema
        self.signals = _Signals()
        self.context = copy_context()
        self.cancelled = threading.Event()

    def run(self):
        started = perf_counter()
        if self.cancelled.is_set():
            try:
                self.signals.finished.emit((self.generation, None, None))
            except RuntimeError:
                pass
            return
        def execute():
            with schema_context(self.schema), request_scope():
                return self.work()
        try:
            result = (self.generation, self.context.run(execute), None)
        except Exception as exc:
            result = (self.generation, None, exc)
        try:
            self.signals.finished.emit(result)
        except RuntimeError:
            pass  # The owning page/application has been destroyed.
        logging.getLogger(__name__).info('background_job work=%s generation=%s duration=%.3f failed=%s', getattr(self.work, '__qualname__', type(self.work).__name__), self.generation, perf_counter() - started, result[2] is not None)


class TaskRunner(QObject):
    """Deliver results on the GUI thread; an older request cannot replace a new one."""
    def __init__(self, parent=None, *, pool=None):
        super().__init__(parent)
        self._pool = pool if pool is not None else _pool
        self._generation = 0
        self._callbacks = {}
        self._jobs = {}
        self._request_key = None
        self._pending_read = None

    def submit(self, work: Callable[[], Result], done: Callable[[Result], None], error: Callable[[Exception], None] | None = None, *, key: object = None) -> None:
        # A periodic refresh of the same context must not invalidate the useful
        # in-flight result. Deliver it, then do at most one requested rerun.
        if key is not None and key == self._request_key and self._generation in self._jobs:
            self._callbacks[self._generation] = (done, error)
            self._pending_read = (work, done, error, key)
            return
        self._request_key = key
        self._pending_read = None
        for generation, job in list(self._jobs.items()):
            job.cancelled.set()
            if self._pool.tryTake(job):
                self._jobs.pop(generation, None)
        self._generation += 1
        generation = self._generation
        self._callbacks.clear()
        self._callbacks[generation] = (done, error)
        job = _Job(generation, work, get_schema())
        self._jobs[generation] = job
        job.signals.finished.connect(self._completed)
        try:
            self._pool.start(job)
        except Exception:
            self._jobs.pop(generation, None)
            self._callbacks.pop(generation, None)
            raise

    def invalidate(self):
        self._request_key = None
        self._pending_read = None
        for generation, job in list(self._jobs.items()):
            job.cancelled.set()
            if self._pool.tryTake(job):
                self._jobs.pop(generation, None)
        self._generation += 1
        self._callbacks.clear()

    def _completed(self, payload):
        generation, result, exc = payload
        self._jobs.pop(generation, None)
        callbacks = self._callbacks.pop(generation, None)
        if callbacks is None or generation != self._generation:
            return
        done, error = callbacks
        pending = self._pending_read
        self._pending_read = None
        if exc is None:
            done(result)
        elif error is not None:
            error(exc)
        if pending is not None and generation == self._generation:
            work, done, error, key = pending
            self.submit(work, done, error, key=key)


class LatestRead:
    """Mark a replaceable read when a caller shares a command submission API."""
    def __init__(self, work):
        self.work = work

    def __call__(self):
        return self.work()


_write_pool = QThreadPool()
_write_pool.setMaxThreadCount(4)
_live_commands = set()


class CommandRunner(QObject):
    """FIFO commands: bounded concurrency, no cancellation or lost completions.

    The runner outlives its page until queued writes finish. Destruction of the
    page suppresses UI callbacks, without silently discarding accepted work.
    """
    def __init__(self, owner=None, capacity=64):
        super().__init__()
        from collections import deque
        self._queue = deque()
        self._jobs = {}
        self._generation = 0
        self._owner_alive = True
        self._capacity = capacity
        if owner is not None:
            owner.destroyed.connect(self._owner_destroyed)

    def _owner_destroyed(self):
        self._owner_alive = False

    def submit(self, work: Callable[[], Result], done: Callable[[Result], None] = lambda _: None, error: Callable[[Exception], None] | None = None) -> None:
        if len(self._queue) + len(self._jobs) >= self._capacity:
            raise RuntimeError("Kolejka operacji jest pełna. Poczekaj na zakończenie poprzednich operacji.")
        self._generation += 1
        job = _Job(self._generation, work, get_schema())
        job.setAutoDelete(False)
        self._queue.append((job, done, error))
        _live_commands.add(self)
        self._start_next()

    def _start_next(self):
        if self._jobs or not self._queue:
            return
        job, done, error = self._queue.popleft()
        self._jobs[job.generation] = (job, done, error)
        job.signals.finished.connect(self._completed)
        try:
            _write_pool.start(job)
        except Exception:
            self._jobs.pop(job.generation, None)
            self._start_next()
            if not self._jobs:
                _live_commands.discard(self)
            raise

    def _completed(self, payload):
        generation, result, exc = payload
        _, done, error = self._jobs.pop(generation)
        self._start_next()
        if not self._jobs and not self._queue:
            _live_commands.discard(self)
        if not self._owner_alive:
            return
        if exc is None:
            done(result)
        elif error is not None:
            error(exc)
