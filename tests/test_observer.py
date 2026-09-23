import time

import pytest

from jev_use.observer import Observer


def _hung_worker(connection):
    connection.recv()
    time.sleep(60)


def _echo_worker(connection):
    while True:
        value = connection.recv()
        if value is None:
            return
        connection.send((True, {"value": value[0]}))


def _crashed_worker(connection):
    connection.recv()
    connection.close()


def test_timeout_kills_worker_and_allows_recovery():
    worker = Observer(timeout=0.3, target=_hung_worker)
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        worker.call("test")
    assert time.monotonic() - started < 3
    assert worker.process is None
    worker.target = _echo_worker
    worker.timeout = 3
    try:
        assert worker.call("recovered") == "recovered"
        pid = worker.process.pid
        assert worker.call("reused") == "reused"
        assert worker.process.pid == pid
    finally:
        worker.close()
    assert worker.process is None


def test_crash_does_not_leave_worker_or_connection():
    worker = Observer(timeout=3, target=_crashed_worker)
    with pytest.raises((EOFError, OSError)):
        worker.call("test")
    assert worker.process is None
    assert worker.connection is None


@pytest.mark.parametrize("timeout", [0, -1, 61, float("nan"), float("inf")])
def test_invalid_timeouts(timeout):
    with pytest.raises(ValueError):
        Observer(timeout)
