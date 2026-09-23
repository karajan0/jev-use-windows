from unittest.mock import patch

import pytest
from PIL import Image

from jev_use import cli, desktop, runner, win32
from jev_use.credentials import Provider


def screen():
    return desktop.Observation(
        win32.Window(12, "fixture", (-100, 10, 100, 110)),
        Image.new("RGB", (200, 100)),
        [],
        [],
        bounds=(-100, 10, 100, 110),
    )


def test_unique_explicit_field_avoids_model_choice():
    state = screen()
    state.targets = [desktop.Target("text", "Name", (-90, 20, -10, 50), "EditControl")]
    actions = runner._actions(state, "Alice", "Name", {}, None)
    assert runner._direct_input(actions, "Name", {}) == "t0"
    assert runner._direct_input(actions, "Nam", {}) is None
    assert runner._direct_input(actions, None, {}) is None


def test_ambiguous_explicit_fields_still_require_model():
    state = screen()
    state.targets = [desktop.Target("text", "Name", (-90, 20, -10, 50), "EditControl")] * 2
    actions = runner._actions(state, "Alice", "Name", {}, None)
    assert runner._direct_input(actions, "Name", {}) is None


def test_existing_success_cannot_prove_new_input_was_saved():
    state = screen()
    state.postcondition_text = ["Saved"]
    state.targets = [desktop.Target("text", "Name", (-90, 20, -10, 50), "EditControl")]
    with (
        patch.object(runner, "TypeSafeClient"),
        patch.object(win32, "activate"),
        patch.object(runner, "observe_desktop", return_value=state),
        patch.object(runner, "_perform", return_value="field_readback"),
        patch.object(runner, "_choose", return_value=("done", 0.99, "p0")),
    ):
        result = runner.run(
            "Save new name",
            state.window,
            Provider("test"),
            text="Alice",
            field="Name",
            expected_visible="Saved",
            delay=0,
            max_steps=2,
        )
    assert not result["goal_achieved"]
    assert result["status"] == "not_achieved"
    assert len(result["actions"]) == 1


def test_settle_returns_immediately_on_change():
    state = screen()
    with (
        patch.object(win32, "select_window", return_value=state.window),
        patch.object(win32, "capture_region", return_value=Image.new("RGB", (200, 100), "white")),
        patch.object(desktop.time, "sleep") as sleep,
    ):
        assert desktop.wait_for_update(state)
    sleep.assert_not_called()


def test_settle_unchanged_frame_reaches_deadline():
    state = screen()
    with (
        patch.object(win32, "select_window", return_value=state.window),
        patch.object(win32, "capture_region", return_value=state.image),
        patch.object(desktop.time, "monotonic", side_effect=[0, 0.1, 0.3]),
        patch.object(desktop.time, "sleep") as sleep,
    ):
        assert not desktop.wait_for_update(state, timeout=0.2)
    assert sleep.call_count == 1


def test_foreground_crop_preserves_negative_coordinates():
    state = screen()
    with (
        patch.object(win32, "select_window", return_value=state.window),
        patch.object(win32, "desktop_bounds", return_value=(-1920, 0, 1920, 1080)),
        patch.object(win32, "capture_region", return_value=state.image) as capture,
        patch.object(desktop, "_finish_observation", return_value=state),
    ):
        assert desktop.observe_desktop() is state
    capture.assert_called_once_with(state.bounds)


def test_reject_foreground_switch_during_uia_read():
    state = screen()
    other = win32.Window(99, "other", state.window.rect)
    with (
        patch.object(win32, "select_window", side_effect=[state.window, state.window, other]),
        patch.object(win32, "desktop_bounds", return_value=(-1920, 0, 1920, 1080)),
        patch.object(win32, "capture_region", return_value=state.image),
        patch.object(desktop, "_finish_observation", return_value=state),
        pytest.raises(RuntimeError, match="changed during observation"),
    ):
        desktop.observe_desktop()


def test_cli_accepts_json_labels():
    args = cli.parser().parse_args(["batch", "Save", "--window", "fixture", "--labels", '["Apply", "Save"]'])
    with (
        patch.object(win32, "enable_dpi_awareness"),
        patch.object(win32, "select_window", return_value=screen().window),
        patch.object(cli, "load", return_value=Provider("test")),
        patch.object(cli, "batch", return_value={"status": "completed"}) as batch,
    ):
        cli.execute(args)
    assert batch.call_args.args[3] == ["Apply", "Save"]


@pytest.mark.parametrize("labels", ['"Save"', "[null]", "[1]", "[]", '[""]'])
def test_cli_rejects_invalid_json_label_shapes(labels):
    args = cli.parser().parse_args(["batch", "Save", "--window", "fixture", "--labels", labels])
    with patch.object(win32, "enable_dpi_awareness"), pytest.raises(ValueError):
        cli.execute(args)


def test_changed_native_target_cannot_fall_back_to_pixels():
    state = screen()
    target = desktop.Target("click", "Save", (-90, 20, -10, 50), "ButtonControl", runtime_id=(1, 2))
    with (
        patch.object(win32, "select_window", return_value=state.window),
        patch.object(win32, "rect_of", return_value=state.window.rect),
        patch.object(runner, "invoke_target", return_value=False),
        patch.object(runner, "target_matches", return_value=False),
        patch.object(win32, "click") as click,
        pytest.raises(RuntimeError, match="target changed"),
    ):
        runner._perform(runner.Action("click", "Save", target), state)
    click.assert_not_called()


def test_partial_ocr_replaces_only_changed_band():
    from jev_use import ocr

    cache = desktop._OcrFrameCache()
    original = Image.new("RGB", (300, 1024), "white")
    changed = original.copy()
    changed.putpixel((10, 520), (0, 0, 0))
    old_words = [
        ocr.TextBox("Top", (0, 10, 30, 30)),
        ocr.TextBox("Old", (0, 520, 30, 540)),
        ocr.TextBox("Bottom", (0, 950, 60, 970)),
    ]
    with patch.object(ocr, "read", side_effect=[old_words, [ocr.TextBox("New", (0, 40, 30, 60))]]) as read:
        cache.read(original, 1, (0, 0, 300, 1024), None)
        result = cache.read(changed, 1, (0, 0, 300, 1024), None)
    assert [word.text for word in result] == ["Top", "New", "Bottom"]
    assert result[1].rect == (0, 520, 30, 540)
    assert read.call_args.args[0].height == 192
