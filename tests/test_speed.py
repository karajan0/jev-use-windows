from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

from jev_use import desktop, ocr, runner, win32
from jev_use.credentials import Provider


def screen(label="Save", proof=None):
    target = desktop.Target("click", label, (10, 10, 80, 40), "ButtonControl", runtime_id=(1, 2))
    return desktop.Observation(
        win32.Window(1, "Test", (0, 0, 200, 200)),
        Image.new("RGB", (200, 200)),
        [target],
        [],
        postcondition_text=proof or [],
    )


def test_explicit_click_runs_without_model_and_still_requires_proof():
    before, after = screen(), screen(proof=["Saved"])
    with (
        patch.object(runner, "TypeSafeClient"),
        patch.object(win32, "activate"),
        patch.object(runner, "observe_desktop", side_effect=[before, after]),
        patch.object(runner, "_perform", return_value="unverifiable") as perform,
        patch.object(runner, "_choose") as choose,
        patch.object(runner, "wait_for_update") as wait,
    ):
        result = runner.run("Save", before.window, Provider("test"), clicks=["Save"], expected_visible="Saved")
    assert result["goal_achieved"] and result["jevCalls"] == 0
    assert perform.call_count == 1
    choose.assert_not_called()
    wait.assert_not_called()


def test_explicit_click_does_not_skip_a_preexisting_success_label():
    before, after = screen(proof=["Saved"]), screen(proof=["Saved"])
    with (
        patch.object(runner, "TypeSafeClient"),
        patch.object(win32, "activate"),
        patch.object(runner, "observe_desktop", side_effect=[before, after]),
        patch.object(runner, "_perform", return_value="unverifiable") as perform,
    ):
        result = runner.run(
            "Save", before.window, Provider("test"), clicks=["Save"], expected_visible="Saved", delay=0, max_steps=1
        )
    assert perform.call_count == 1
    assert not result["goal_achieved"]


def test_ambiguous_explicit_click_never_guesses():
    before = screen()
    before.targets.append(desktop.Target("click", "Save", (100, 10, 170, 40), "ButtonControl", runtime_id=(1, 3)))
    with (
        patch.object(runner, "TypeSafeClient"),
        patch.object(win32, "activate"),
        patch.object(runner, "observe_desktop", return_value=before),
        patch.object(runner, "_perform") as perform,
        patch.object(runner, "_choose") as choose,
    ):
        result = runner.run("Save", before.window, Provider("test"), clicks=["Save"], expected_visible="Saved")
    assert not result["goal_achieved"] and result["status"] == "uncertain"
    perform.assert_not_called()
    choose.assert_not_called()


def test_slow_ui_gets_observed_again_without_resending():
    before, unchanged, after = screen(), screen(), screen(proof=["Saved"])
    with (
        patch.object(runner, "TypeSafeClient"),
        patch.object(win32, "activate"),
        patch.object(runner, "observe_desktop", side_effect=[before, unchanged, after]),
        patch.object(runner, "_perform", return_value="unverifiable") as perform,
        patch.object(runner, "wait_for_update", return_value=True) as wait,
    ):
        result = runner.run("Save", before.window, Provider("test"), clicks=["Save"], expected_visible="Saved")
    assert result["goal_achieved"]
    assert perform.call_count == wait.call_count == 1


def test_tile_cache_invalidates_pixels_language_and_closes():
    calls = []

    async def recognize(image, language):
        calls.append(language)
        return [ocr.TextBox(str(image.getpixel((20, 20))), (20, 20, 40, 40))]

    ocr.close_worker()
    try:
        with patch.object(ocr, "_read", side_effect=recognize):
            image = Image.new("RGB", (2000, 100), "black")
            first = ocr.read(image, "en-US")
            count = len(calls)
            assert ocr.read(image.copy(), "en-US") == first
            assert len(calls) == count
            image.putpixel((10, 10), (255, 0, 0))
            ocr.read(image, "en-US")
            assert len(calls) == count + 1
            ocr.read(image, "ko")
            assert len(calls) > count + 1
    finally:
        ocr.close_worker()
    assert not hasattr(ocr._worker, "tiles")


def test_verified_native_fields_share_one_observation_then_save():
    first = desktop.Target("text", "First", (10, 50, 90, 70), "EditControl", runtime_id=(1, 10))
    second = desktop.Target("text", "Second", (10, 90, 90, 110), "EditControl", runtime_id=(1, 11))
    before, filled, final = screen(), screen(), screen(proof=["Saved"])
    before.targets.extend([first, second])
    filled.targets.extend([replace(first, value="A"), replace(second, value="B")])
    final.targets = filled.targets
    with (
        patch.object(runner, "TypeSafeClient"),
        patch.object(win32, "activate"),
        patch.object(runner, "observe_desktop", side_effect=[before, filled, final]) as observe,
        patch.object(runner, "_perform", side_effect=["field_readback", "field_readback", "unverifiable"]) as perform,
        patch.object(runner, "_choose") as choose,
    ):
        result = runner.run(
            "Fill and save",
            before.window,
            Provider("test"),
            fills={"First": "A", "Second": "B"},
            clicks=["Save"],
            expected_visible="Saved",
            delay=0,
        )
    assert result["goal_achieved"] and result["jevCalls"] == 0
    assert observe.call_count == 3
    assert [call.args[0].target.label for call in perform.call_args_list] == ["First", "Second", "Save"]
    choose.assert_not_called()


def test_batch_readback_failure_stops_before_save():
    before = screen()
    before.targets.extend(
        [
            desktop.Target("text", name, (10, y, 90, y + 20), "EditControl", runtime_id=(1, y))
            for name, y in (("First", 50), ("Second", 90))
        ]
    )
    with (
        patch.object(runner, "TypeSafeClient"),
        patch.object(win32, "activate"),
        patch.object(runner, "observe_desktop", return_value=before),
        patch.object(runner, "_perform", side_effect=["field_readback", "suspected_noop"]) as perform,
    ):
        result = runner.run(
            "Fill and save",
            before.window,
            Provider("test"),
            fills={"First": "A", "Second": "B"},
            clicks=["Save"],
            expected_visible="Saved",
            delay=0,
        )
    assert not result["goal_achieved"] and result["status"] == "uncertain"
    assert [call.args[0].kind for call in perform.call_args_list] == ["type", "type"]


def test_native_value_write_is_read_back_without_clipboard_input():
    target = desktop.Target("text", "First", (10, 10, 100, 40), "EditControl", runtime_id=(1, 2))
    pattern = Mock(IsReadOnly=False, Value="정확한 값")
    pattern.SetValue.return_value = True
    control = SimpleNamespace(IsPassword=False, GetValuePattern=lambda: pattern)
    with patch.object(desktop, "_matching_control", return_value=control):
        assert desktop.set_target_value(target, "정확한 값") is True
    pattern.SetValue.assert_called_once_with("정확한 값", waitTime=0)


def test_failed_native_write_is_not_retried_with_keyboard():
    import pytest

    state = screen()
    target = desktop.Target("text", "First", (10, 10, 100, 40), "EditControl", runtime_id=(1, 2))
    action = runner.Action("type", "Fill First", target, "A")
    with (
        patch.object(win32, "select_window", return_value=state.window),
        patch.object(win32, "rect_of", return_value=state.window.rect),
        patch.object(runner, "_validate_target"),
        patch.object(runner, "set_target_value", side_effect=RuntimeError("dispatched failure")),
        patch.object(win32, "click") as click,
        patch.object(win32, "type_text") as type_text,
        pytest.raises(RuntimeError, match="dispatched failure"),
    ):
        runner._perform(action, state)
    click.assert_not_called()
    type_text.assert_not_called()


def test_readonly_native_field_is_rejected_before_dispatch():
    import pytest

    target = desktop.Target("text", "First", (10, 10, 100, 40), "EditControl", runtime_id=(1, 2))
    pattern = Mock(IsReadOnly=True)
    control = SimpleNamespace(IsPassword=False, GetValuePattern=lambda: pattern)
    with patch.object(desktop, "_matching_control", return_value=control), pytest.raises(ValueError, match="read-only"):
        desktop.set_target_value(target, "A")
    pattern.SetValue.assert_not_called()


def test_missing_supplied_field_cannot_be_skipped_to_save():
    before = screen()
    with (
        patch.object(runner, "TypeSafeClient"),
        patch.object(win32, "activate"),
        patch.object(runner, "observe_desktop", return_value=before),
        patch.object(runner, "_perform") as perform,
    ):
        result = runner.run(
            "Fill and save",
            before.window,
            Provider("test"),
            fills={"Missing": "A"},
            clicks=["Save"],
            expected_visible="Saved",
        )
    assert not result["goal_achieved"] and result["status"] == "uncertain"
    perform.assert_not_called()


def test_existing_exact_field_value_needs_no_retyping():
    before, final = screen(), screen(proof=["Saved"])
    before.targets.append(desktop.Target("text", "First", (10, 80, 100, 100), "EditControl", value="A"))
    final.targets = before.targets
    with (
        patch.object(runner, "TypeSafeClient"),
        patch.object(win32, "activate"),
        patch.object(runner, "observe_desktop", side_effect=[before, final]),
        patch.object(runner, "_perform", return_value="unverifiable") as perform,
    ):
        result = runner.run(
            "Fill and save",
            before.window,
            Provider("test"),
            fills={"First": "A"},
            clicks=["Save"],
            expected_visible="Saved",
            delay=0,
        )
    assert result["goal_achieved"] and result["jevCalls"] == 0
    assert [call.args[0].kind for call in perform.call_args_list] == ["click"]
