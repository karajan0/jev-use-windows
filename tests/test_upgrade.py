from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from jev_use import desktop, ocr, runner, win32
from jev_use.credentials import Provider
from jev_use.metrics import measured_task, timed


@pytest.fixture
def screen():
    return desktop.Observation(
        win32.Window(123, "Test", (0, 0, 100, 100)),
        Image.new("RGB", (100, 100)),
        [],
        ["draft text"],
        ["draft text"],
        postcondition_text=["Saved"],
    )


def test_draft_is_not_completion_evidence(screen):
    assert list(runner._evidence(screen).values()) == ["No visible text proves the goal.", "Saved"]
    screen.postcondition_text = []
    assert set(runner._evidence(screen)) == {"none"}


@pytest.mark.parametrize(
    "values", [("hello", {}, None), (None, {"Name": "A"}, None), (None, {}, "https://example.com")]
)
def test_missing_additional_input_remains_available(screen, values):
    text, fills, url = values
    assert "needs_input" in runner._actions(screen, text, None, fills, url)


def test_run_can_request_more_input_with_supplied_text(screen):
    with (
        patch.object(runner, "TypeSafeClient"),
        patch.object(win32, "activate"),
        patch.object(runner, "observe_desktop", return_value=screen),
        patch.object(runner, "_choose", return_value=("needs_input", 0.9, "none")),
    ):
        result = runner.run("Fill fields", screen.window, Provider("test"), text="A")
    assert result["status"] == "needs_input"
    assert result["goal_achieved"] is False


def test_batch_never_clicks_a_stale_target(screen):
    target = desktop.Target("click", "Save", (10, 10, 20, 20), "ButtonControl")
    with (
        patch.object(runner, "observe_controls", return_value=(screen.window, [target])),
        patch.object(win32, "activate"),
        patch.object(win32, "select_window", return_value=screen.window),
        patch.object(win32, "rect_of", return_value=screen.window.rect),
        patch.object(runner, "target_matches", return_value=False),
        patch.object(runner, "invoke_target") as invoke,
        patch.object(win32, "click") as click,
    ):
        result = runner.batch("Save", screen.window, Provider("test"), ["Save"])
    invoke.assert_not_called()
    click.assert_not_called()
    assert result["status"] == "uncertain"
    assert not result["goal_achieved"]


@pytest.mark.parametrize("change", ["position", "disabled", "offscreen", "identity"])
def test_matching_control_rejects_changed_target(change):
    import uiautomation as auto

    target = desktop.Target("click", "Save", (10, 10, 20, 20), "ButtonControl", runtime_id=(1, 2))
    control = MagicMock()
    control.ControlTypeName = "ButtonControl"
    control.Name = "Save"
    control.BoundingRectangle = SimpleNamespace(left=11 if change == "position" else 10, top=10, right=20, bottom=20)
    control.IsEnabled = change != "disabled"
    control.IsOffscreen = change == "offscreen"
    control.GetRuntimeId.return_value = (1, 3) if change == "identity" else (1, 2)
    with patch.object(auto, "ControlFromPoint", return_value=control):
        assert not desktop.target_matches(target)


def test_unchanged_frame_skips_ocr():
    cache = desktop._OcrFrameCache()
    image = Image.new("RGB", (20, 20))
    with patch.object(ocr, "read", return_value=[]) as read:
        cache.read(image, 1, (0, 0, 20, 20), None)
        cache.read(image.copy(), 1, (0, 0, 20, 20), None)
    assert read.call_count == 1


def test_ocr_event_loop_reused_and_closed():
    import asyncio

    loops = []

    async def fake_read(image, language):
        loops.append(asyncio.get_running_loop())
        return []

    ocr.close_worker()
    with patch.object(ocr, "_read", fake_read):
        ocr.read(Image.new("RGB", (10, 10)))
        ocr.read(Image.new("RGB", (10, 10)))
    ocr.close_worker()
    assert loops[0] is loops[1]
    assert loops[0].is_closed()


def test_metrics_are_task_local_and_record_failures():
    @timed("operation")
    def operation():
        raise ValueError("test")

    @measured_task
    def task():
        with pytest.raises(ValueError):
            operation()
        return {"status": "uncertain"}

    for _ in range(2):
        result = task()
        assert result["timings"]["operation"]["calls"] == 1
        assert result["timings"]["operation"]["seconds"] >= 0
        assert not result["goal_achieved"]


def test_expected_file_must_change(tmp_path, screen):
    path = tmp_path / "output.txt"
    path.write_text("old")
    before = runner._file_state(path)
    assert runner._postcondition(screen, None, path, before) == (False, None)
    path.write_text("new content")
    assert runner._postcondition(screen, "Saved", path, before)[0]


@pytest.mark.parametrize("confidence, expected", [(0.9, True), (0.1, False)])
def test_valid_batch_verifies_result(screen, confidence, expected):
    target = desktop.Target("click", "Save", (10, 10, 20, 20), "ButtonControl")
    response = SimpleNamespace(
        answers={
            "verdict": SimpleNamespace(choice="achieved", confidence=confidence),
            "proof": SimpleNamespace(choice="p0"),
        }
    )
    with (
        patch.object(runner, "observe_controls", return_value=(screen.window, [target])),
        patch.object(win32, "activate"),
        patch.object(win32, "select_window", return_value=screen.window),
        patch.object(win32, "rect_of", return_value=screen.window.rect),
        patch.object(runner, "target_matches", return_value=True),
        patch.object(runner, "invoke_target", return_value=True),
        patch.object(runner, "observe_desktop", return_value=screen),
        patch.object(runner, "TypeSafeClient") as client,
    ):
        client.return_value.__enter__.return_value.system_one.return_value = response
        result = runner.batch("Save", screen.window, Provider("test"), ["Save"], delay=0)
    assert result["goal_achieved"] is expected


def test_native_ocr_engine_is_reused():
    if not ocr.available_languages():
        pytest.skip("No Windows OCR language installed on this test machine")
    ocr.close_worker()
    try:
        assert ocr._engine(None) is ocr._engine(None)
    finally:
        ocr.close_worker()
