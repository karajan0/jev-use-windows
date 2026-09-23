import multiprocessing
import uuid

import pytest

from jev_use.input_lock import InputBusyError, input_lock


def _hold_lock(name, pipe):
    with input_lock(name):
        pipe.send("locked")
        pipe.recv()


def test_second_process_cannot_send_input_until_owner_releases():
    name = "Local\\JevTest." + uuid.uuid4().hex
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe()
    process = ctx.Process(target=_hold_lock, args=(name, child))
    process.start()
    child.close()
    try:
        assert parent.poll(5)
        assert parent.recv() == "locked"
        with pytest.raises(InputBusyError), input_lock(name):
            pass
        parent.send("release")
        process.join(3)
        assert process.exitcode == 0
        with input_lock(name):
            pass
    finally:
        if process.is_alive():
            process.terminate()
            process.join(2)
        parent.close()
        process.close()
