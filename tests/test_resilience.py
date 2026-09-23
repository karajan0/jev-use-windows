import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from test_speed import screen

from jev_use import desktop, observer, runner, uia_cache, win32
from jev_use.credentials import Provider


def _dispatched_then_hung(connection):
    connection.send("ready")
    operation, args, _kwargs = connection.recv()
    args[0].write_text(operation)
    time.sleep(60)


def test_dispatched_native_timeout_terminates_worker_without_replay(tmp_path):
    marker = tmp_path / "dispatch.txt"
    worker = observer.Observer(timeout=0.3, target=_dispatched_then_hung)
    worker._start()
    assert worker.connection.poll(10)
    assert worker.connection.recv() == "ready"
    token = observer._session.set(worker)
    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError, match="do not resend"):
            observer._call("invoke_target", marker)
        assert time.monotonic() - started < 4
        assert marker.read_text() == "invoke_target"
        assert worker.process is None and worker.connection is None
    finally:
        observer._session.reset(token)
        worker.close()


def test_native_dispatch_rechecks_foreground_in_worker():
    state = screen()
    with (
        patch.object(win32, "enable_dpi_awareness"),
        patch.object(win32, "select_window", return_value=replace(state.window, handle=2)),
        patch.object(desktop, "invoke_target") as invoke,
        pytest.raises(RuntimeError, match="no input was sent"),
    ):
        observer._dispatch("invoke_target", (state.targets[0],), {"_expected_window": state.window})
    invoke.assert_not_called()


def test_run_stops_after_native_timeout_without_fallback_or_replay():
    before = screen()
    with (
        patch.object(runner, "TypeSafeClient"),
        patch.object(win32, "activate"),
        patch.object(win32, "select_window", return_value=before.window),
        patch.object(win32, "rect_of", return_value=before.window.rect),
        patch.object(runner, "observe_desktop", return_value=before),
        patch.object(runner, "target_matches", return_value=True),
        patch.object(runner, "invoke_target", side_effect=TimeoutError("dispatched, outcome unknown")) as invoke,
        patch.object(win32, "click") as click,
    ):
        result = runner.run("Save", before.window, Provider("test"), clicks=["Save"])
    assert result["status"] == "uncertain" and not result["goal_achieved"]
    assert result["attempted"]
    invoke.assert_called_once()
    click.assert_not_called()


def test_delayed_button_does_not_consume_action_budget_or_call_model():
    absent = screen("Loading")
    ready, done = screen(), screen(proof=["Saved"])
    with (
        patch.object(runner, "TypeSafeClient"),
        patch.object(win32, "activate"),
        patch.object(runner, "observe_desktop", side_effect=[absent, absent, ready, done]),
        patch.object(runner, "_perform", return_value="unverifiable") as perform,
        patch.object(runner, "_choose") as choose,
        patch.object(runner.time, "sleep"),
    ):
        result = runner.run(
            "Save", ready.window, Provider("test"), clicks=["Save"], expected_visible="Saved", max_steps=1, delay=0
        )
    assert result["goal_achieved"] and result["jevCalls"] == 0
    perform.assert_called_once()
    choose.assert_not_called()


def test_missing_button_expires_without_input():
    absent = screen("Loading")
    with (
        patch.object(runner, "TypeSafeClient"),
        patch.object(win32, "activate"),
        patch.object(runner, "observe_desktop", return_value=absent),
        patch.object(runner.time, "monotonic", side_effect=[0, 1]),
        patch.object(runner, "_perform") as perform,
        patch.object(runner, "_choose") as choose,
    ):
        result = runner.run("Save", absent.window, Provider("test"), clicks=["Save"], target_timeout=0.5)
    assert result["status"] == "uncertain" and "Timed out" in result["reason"]
    perform.assert_not_called()
    choose.assert_not_called()


def test_appearing_duplicate_buttons_stop_without_click():
    absent, duplicate = screen("Loading"), screen()
    duplicate.targets.append(replace(duplicate.targets[0], rect=(100, 10, 180, 40), runtime_id=(1, 3)))
    with (
        patch.object(runner, "TypeSafeClient"),
        patch.object(win32, "activate"),
        patch.object(runner, "observe_desktop", side_effect=[absent, duplicate]),
        patch.object(runner, "_perform") as perform,
        patch.object(runner.time, "sleep"),
    ):
        result = runner.run("Save", absent.window, Provider("test"), clicks=["Save"])
    assert result["status"] == "uncertain" and "ambiguous" in result["reason"]
    perform.assert_not_called()


class Control:
    IsOffscreen = False
    IsEnabled = True
    IsPassword = False
    HasKeyboardFocus = False
    BoundingRectangle = SimpleNamespace(left=10, top=10, right=80, bottom=40)

    def __init__(self, name, role="ButtonControl", child=None, sibling=None):
        self.Name, self.ControlTypeName = name, role
        self.child, self.sibling = child, sibling

    def GetFirstChildControl(self):
        return self.child

    def GetNextSiblingControl(self):
        return self.sibling

    def GetRuntimeId(self):
        return (id(self),)


def tree(count):
    child = None
    for index in reversed(range(count)):
        child = Control("Save" if index in {0, count - 1} else f"Item {index}", sibling=child)
    return Control("Window", "WindowControl", child=child)


def test_dense_tree_finds_duplicate_beyond_old_node_and_target_limits():
    with patch.object(uia_cache, "root", return_value=tree(401)):
        targets, _ = desktop._uia_targets(1, (0, 0, 200, 200), target_labels=["Save"])
    assert len(targets) == 401
    state = screen()
    state.targets = targets
    assert runner._direct_click(runner._actions(state, None, None, {}, None), "Save") is None


def test_target_search_expands_beyond_old_depth_limit():
    child = Control("Save")
    for _ in range(8):
        child = Control("Panel", "PaneControl", child=child)
    with patch.object(uia_cache, "root", return_value=child):
        targets, _ = desktop._uia_targets(1, (0, 0, 200, 200), target_labels=["Save"])
    assert [target.label for target in targets] == ["Save"]


def test_exhausted_target_search_does_not_claim_unique_match():
    with (
        patch.object(uia_cache, "root", return_value=tree(2001)),
        pytest.raises(RuntimeError, match="uniqueness cannot be verified"),
    ):
        desktop._uia_targets(1, (0, 0, 200, 200), target_labels=["Save"])


@pytest.mark.parametrize("timeout", [-1, 61, float("nan"), float("inf")])
def test_invalid_target_timeouts(timeout):
    with pytest.raises(ValueError, match="Target timeout"):
        runner.run("Save", screen().window, Provider("test"), target_timeout=timeout)
