"""Reusable, killable worker for observation and native UI Automation calls."""

import multiprocessing
import time
from contextlib import suppress
from contextvars import ContextVar
from functools import wraps

from .metrics import measured_task, merge_timings

_session: ContextVar[object] = ContextVar("jev_observer", default=None)


@measured_task
def _dispatch(operation, args, kwargs):
    from . import desktop, win32

    win32.enable_dpi_awareness()
    functions = {
        "desktop": desktop.observe_desktop,
        "window": desktop.observe,
        "controls": desktop.observe_controls,
        "target_matches": desktop.target_matches,
        "invoke_target": desktop.invoke_target,
        "set_target_value": desktop.set_target_value,
        "read_focused_value": desktop.read_focused_value,
    }
    expected = kwargs.pop("_expected_window", None)
    if expected is not None:
        foreground = win32.select_window(None)
        if foreground.handle != expected.handle or foreground.rect != expected.rect:
            raise RuntimeError("Foreground window changed before native input; no input was sent")
    return {"value": functions[operation](*args, **kwargs)}


def _worker_main(connection):
    # Set the apartment before importing comtypes/uiautomation in this process.
    import sys

    sys.coinit_flags = 0  # COINIT_MULTITHREADED
    try:
        while True:
            request = connection.recv()
            if request is None:
                return
            try:
                result = _dispatch(*request)
                connection.send((True, result))
            except Exception as exc:
                connection.send((False, f"{type(exc).__name__}: {exc}"))
    except (EOFError, BrokenPipeError, OSError):
        pass
    finally:
        connection.close()
        from . import desktop, ocr

        desktop._ocr_pool.submit(ocr.close_worker).result()
        desktop._ocr_pool.shutdown(wait=True)


class Observer:
    def __init__(self, timeout=5.0, *, target=_worker_main):
        if not 0 < timeout <= 60:
            raise ValueError("Observation timeout must be greater than 0 and at most 60 seconds")
        self.timeout = timeout
        self.target = target
        self.process = None
        self.connection = None

    def _start(self):
        ctx = multiprocessing.get_context("spawn")
        self.connection, child = ctx.Pipe()
        self.process = ctx.Process(target=self.target, args=(child,), daemon=True)
        try:
            self.process.start()
        except BaseException:
            self.connection.close()
            self.connection = None
            self.process = None
            raise
        finally:
            child.close()

    def call(self, operation, *args, **kwargs):
        if self.process is None:
            self._start()
        started = time.monotonic()
        try:
            self.connection.send((operation, args, kwargs))
            if not self.connection.poll(max(0, self.timeout - (time.monotonic() - started))):
                raise TimeoutError(
                    f"{operation} exceeded {self.timeout:g}s; worker terminated. "
                    "Native input may already have been dispatched; do not resend automatically."
                )
            success, result = self.connection.recv()
        except (TimeoutError, EOFError, BrokenPipeError, OSError):
            self.close(force=True)
            raise
        if not success:
            raise RuntimeError(result)
        merge_timings(result.get("timings", {}))
        return result["value"]

    def close(self, *, force=False):
        process, connection = self.process, self.connection
        self.process = self.connection = None
        if process is None:
            return
        try:
            if not force and process.is_alive():
                with suppress(BrokenPipeError, OSError):
                    connection.send(None)
                process.join(0.2)
            if process.is_alive():
                process.terminate()
                process.join(1)
            if process.is_alive():
                process.kill()
                process.join(1)
        finally:
            connection.close()
            if not process.is_alive():
                process.close()


def isolated(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        timeout = kwargs.pop("observation_timeout", 5.0)
        worker = Observer(timeout)
        token = _session.set(worker)
        try:
            return function(*args, **kwargs)
        finally:
            worker.close()
            _session.reset(token)

    return wrapped


def _call(operation, *args, **kwargs):
    worker = _session.get()
    if worker is not None:
        return worker.call(operation, *args, **kwargs)
    worker = Observer()
    try:
        return worker.call(operation, *args, **kwargs)
    finally:
        worker.close()


def observe_desktop(**kwargs):
    return _call("desktop", **kwargs)


def observe(window, **kwargs):
    return _call("window", window, **kwargs)


def observe_controls(window, **kwargs):
    return _call("controls", window, **kwargs)


def target_matches(target):
    if target.role in {"OCR", "Visual"}:
        return False
    return _call("target_matches", target)


def invoke_target(target, window=None):
    if target.role not in {"ButtonControl", "HyperlinkControl", "MenuItemControl"}:
        return False
    return _call("invoke_target", target, _expected_window=window)


def set_target_value(target, value, window=None):
    if target.role not in {"EditControl", "ComboBoxControl"} or not target.runtime_id:
        return None
    return _call("set_target_value", target, value, _expected_window=window)


def read_focused_value(target):
    return _call("read_focused_value", target)
