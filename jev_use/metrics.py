"""Task-local timings; no screen contents or supplied values are recorded."""

from contextvars import ContextVar
from functools import wraps
from time import perf_counter

_active: ContextVar[dict | None] = ContextVar("jev_metrics", default=None)


def merge_timings(incoming):
    metrics = _active.get()
    if metrics is not None:
        for name, value in incoming.items():
            item = metrics.setdefault(name, {"calls": 0, "seconds": 0.0})
            item["calls"] += value["calls"]
            item["seconds"] += value["seconds"]


def timed(stage):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            metrics = _active.get()
            started = perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                if metrics is not None:
                    item = metrics.setdefault(stage, {"calls": 0, "seconds": 0.0})
                    item["calls"] += 1
                    item["seconds"] += perf_counter() - started

        return wrapped

    return decorate


def measured_task(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        metrics = {}
        token = _active.set(metrics)
        try:
            result = function(*args, **kwargs)
            result["timings"] = {
                name: {"calls": value["calls"], "seconds": round(value["seconds"], 6)}
                for name, value in metrics.items()
            }
            result["goal_achieved"] = result.get("status") == "completed"
            return result
        finally:
            _active.reset(token)

    return wrapped
