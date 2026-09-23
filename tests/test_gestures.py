from unittest.mock import MagicMock, patch

import pytest

from jev_use import cli, gestures, win32


@pytest.mark.parametrize("failure", [RuntimeError("focus changed"), KeyboardInterrupt()])
def test_held_chord_releases_all_keys_on_interruption(failure):
    api = MagicMock()
    api.GetAsyncKeyState.return_value = 0
    with (
        patch.object(win32, "_user32", return_value=api),
        patch.object(gestures, "_foreground", side_effect=[None, failure]),
        patch.object(win32, "_send") as send,
        pytest.raises(type(failure)),
    ):
        gestures.hold("ctrl+a", 0.2, 1)
    released = send.call_args.args[0]
    assert [(event.value.ki.wVk, event.value.ki.dwFlags) for event in released] == [(65, 2), (17, 2)]


def test_partial_send_still_releases_the_attempted_key():
    api = MagicMock()
    api.GetAsyncKeyState.return_value = 0
    with (
        patch.object(win32, "_user32", return_value=api),
        patch.object(gestures, "_foreground"),
        patch.object(win32, "_send", side_effect=[RuntimeError("partial input"), None]) as send,
        pytest.raises(RuntimeError),
    ):
        gestures.hold("space", 0.2, 1)
    assert send.call_args.args[0][0].value.ki.dwFlags == 2


def test_existing_user_key_is_not_released_or_modified():
    api = MagicMock()
    api.GetAsyncKeyState.return_value = 0x8000
    with (
        patch.object(win32, "_user32", return_value=api),
        patch.object(gestures, "_foreground"),
        patch.object(win32, "_send") as send,
        pytest.raises(RuntimeError, match="already held"),
    ):
        gestures.hold("space", 0.2, 1)
    send.assert_not_called()


def test_drag_releases_mouse_after_focus_change():
    api = MagicMock()
    api.GetAsyncKeyState.return_value = 0
    api.SetCursorPos.return_value = True
    with (
        patch.object(win32, "_user32", return_value=api),
        patch.object(gestures, "_foreground", side_effect=[None, RuntimeError("focus")]),
        patch.object(win32, "_send") as send,
        pytest.raises(RuntimeError),
    ):
        gestures.drag((10, 20), (100, 200), 0.2, 1)
    assert send.call_args_list[0].args[0][0].value.mi.dwFlags == 2
    assert send.call_args_list[-1].args[0][0].value.mi.dwFlags == 4


@pytest.mark.parametrize("entry", ["space=0", "space=2001", "space=nan", "space=inf", "badkey=10", "ctrl+ctrl=10"])
def test_invalid_holds(entry):
    with pytest.raises(ValueError):
        cli._holds([entry])


def test_explicit_hold_duration_and_chord():
    assert cli._holds(["Ctrl+Shift+A=250"]) == {"ctrl+shift+a": 0.25}
