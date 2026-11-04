"""Bounded Qt background jobs with stale-result rejection and schema isolation."""
from __future__ import annotations

from contextvars import copy_context
import threading
from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal
from app.config import get_schema, schema_context


_pool = QThreadPool()
_pool.setMaxThreadCount(4)


class _Signals(QObject):
    finished = pyqtSignal(object)


class _Job(QRunnable):
    def __init__(self, generation, work, schema):
        super().__init__()
        self.generation, self.work, self.schema = generation, work, schema
        self.signals = _Signals()
        self.context = copy_context()
        self.cancelled = threading.Event()

    def run(self):
        if self.cancelled.is_set():
            try:
                self.signals.finished.emit((self.generation, None, None))
            except RuntimeError:
                pass
            return
        def execute():
            with schema_context(self.schema):
                return self.work()
        try:
            result = (self.generation, self.context.run(execute), None)
        except Exception as exc:
            result = (self.generation, None, exc)
        try:
            self.signals.finished.emit(result)
        except RuntimeError:
            pass  # The owning page/application has been destroyed.


class TaskRunner(QObject):
    """Deliver results on the GUI thread; an older request cannot replace a new one."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._generation = 0
        self._callbacks = {}
        self._jobs = {}

    def submit(self, work, done, error=None):
        for job in self._jobs.values():
            job.cancelled.set()
        self._generation += 1
        generation = self._generation
        self._callbacks.clear()
        self._callbacks[generation] = (done, error)
        job = _Job(generation, work, get_schema())
        self._jobs[generation] = job
        job.signals.finished.connect(self._completed)
        _pool.start(job)

    def invalidate(self):
        for job in self._jobs.values():
            job.cancelled.set()
        self._generation += 1
        self._callbacks.clear()

    def _completed(self, payload):
        generation, result, exc = payload
        self._jobs.pop(generation, None)
        callbacks = self._callbacks.pop(generation, None)
        if callbacks is None or generation != self._generation:
            return
        done, error = callbacks
        if exc is None:
            done(result)
        elif error is not None:
            error(exc)
