from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PIL import Image, ImageDraw

from jev_use import desktop, runner, visual, win32
from jev_use.credentials import Provider


def icon(size=48):
    image = Image.new("RGB", (48, 48), (20, 45, 85))
    draw = ImageDraw.Draw(image)
    draw.rectangle((2, 2, 45, 45), outline="white", width=2)
    draw.polygon([(13, 10), (36, 24), (13, 38)], fill=(40, 230, 170))
    draw.rectangle((5, 5, 9, 9), fill="orange")
    return image.resize((size, size), Image.Resampling.BILINEAR)


def scene(x=40, y=40, size=48, background="black"):
    image = Image.new("RGB", (400, 240), background)
    image.paste(icon(size), (x, y))
    return image


def observation(image, target=None):
    return desktop.Observation(
        win32.Window(1, "Canvas", (0, 0, 400, 240)), image, [target] if target else [], [], bounds=(0, 0, 400, 240)
    )


@pytest.mark.parametrize("x,y,size", [(40, 40, 48), (220, 120, 48), (120, 80, 60), (120, 80, 36)])
def test_icon_location_and_scale(x, y, size):
    match = visual.unique_match(scene(x, y, size), icon())
    assert match is not None
    assert match.rect == (x, y, x + size, y + size)


def test_duplicate_icons_are_rejected():
    image = scene()
    image.paste(icon(), (240, 40))
    assert visual.unique_match(image, icon()) is None


def test_blank_and_missing_reference_do_not_match():
    assert visual.unique_match(scene(), Image.new("RGB", (30, 30), "red")) is None
    assert visual.unique_match(Image.new("RGB", (400, 240)), icon()) is None


def test_occluded_icon_is_rejected():
    image = scene()
    ImageDraw.Draw(image).rectangle((40, 40, 70, 80), fill="red")
    assert visual.unique_match(image, icon()) is None


def test_background_animation_is_not_semantic_progress():
    target = desktop.Target("click", "launch", (40, 40, 88, 88), "Visual")
    before = observation(scene(), target)
    image = scene()
    ImageDraw.Draw(image).rectangle((240, 150, 399, 239), fill="orange")
    after = observation(image, target)
    assert runner._snapshot_id(before) == runner._snapshot_id(after)
    assert visual.transition(before, after, target)["effect"] == "background_change_only"


def test_control_change_is_reported_separately_from_background():
    target = desktop.Target("click", "launch", (40, 40, 88, 88), "Visual")
    before = observation(scene(), target)
    image = scene()
    ImageDraw.Draw(image).rectangle((45, 45, 80, 80), fill="orange")
    after = observation(image, target)
    assert visual.transition(before, after, target)["effect"] == "target_region_changed"
    after.postcondition_text = ["Done"]
    assert visual.transition(before, after, target)["effect"] == "state_changed"


def test_visual_target_reacquired_after_movement_and_zoom():
    target = desktop.Target("click", "launch", (40, 40, 88, 88), "Visual")
    new = replace(target, rect=(220, 120, 280, 180))
    assert visual.reacquire(target, observation(scene(), target), observation(scene(220, 120, 60), new)) == new


def test_native_reacquisition_requires_runtime_identity():
    target = desktop.Target("click", "Save", (40, 40, 88, 88), "ButtonControl", runtime_id=(1, 2))
    replacement = replace(target, rect=(220, 120, 268, 168), runtime_id=(1, 3))
    before = observation(scene(), target)
    after = observation(scene(220, 120), replacement)
    assert visual.reacquire(target, before, after) is None
    after.targets = [replace(replacement, runtime_id=(1, 2))]
    assert visual.reacquire(target, before, after) == after.targets[0]


def test_noise_does_not_count_as_changed_region():
    image = scene()
    noisy = image.copy()
    noisy.putpixel((399, 239), (255, 255, 255))
    assert visual.changed_regions(image, noisy) == []


def test_reference_targets_preserve_negative_screen_origin(tmp_path):
    path = tmp_path / "launch.png"
    icon().save(path)
    targets = visual.locate_targets(scene(), (-400, 10, 0, 250), {"launch": str(path)})
    assert targets[0].rect == (-360, 50, -312, 98)


def test_stale_target_is_reobserved_before_any_retry():
    old = desktop.Target("click", "Save", (40, 40, 88, 88), "ButtonControl", runtime_id=(1, 2))
    new = replace(old, rect=(220, 120, 268, 168))
    before, after = observation(scene(), old), observation(scene(220, 120), new)
    final = observation(scene(220, 120), new)
    final.postcondition_text = ["Saved"]
    with (
        patch.object(runner, "TypeSafeClient"),
        patch.object(win32, "activate"),
        patch.object(runner, "observe_desktop", side_effect=[before, after, final]),
        patch.object(runner, "_choose", return_value=("c0", 1, "none")),
        patch.object(runner, "_perform", side_effect=[runner.StaleTargetError("moved"), "unverifiable"]) as perform,
    ):
        result = runner.run("Save", before.window, Provider("test"), expected_visible="Saved", delay=0)
    assert result["goal_achieved"]
    assert perform.call_args.args[0].target == new
    assert result["transitions"][0]["effect"] == "target_reacquired"


def test_region_wait_ignores_remote_animation():
    target = desktop.Target("click", "launch", (40, 40, 88, 88), "Visual")
    state = observation(scene(), target)
    # capture_region returns only the requested target neighborhood, which is unchanged.
    crop = state.image.crop((16, 16, 112, 112))
    with (
        patch.object(win32, "select_window", return_value=state.window),
        patch.object(win32, "capture_region", return_value=crop) as capture,
        patch.object(desktop.time, "monotonic", side_effect=[0, 1]),
    ):
        assert not desktop.wait_for_update(state, region=target.rect)
    capture.assert_called_once_with((16, 16, 112, 112))


def test_animated_background_does_not_allow_repeating_noop_click():
    target = desktop.Target("click", "launch", (40, 40, 88, 88), "Visual")
    before = observation(scene(), target)
    image = scene()
    ImageDraw.Draw(image).rectangle((240, 150, 399, 239), fill="orange")
    after = observation(image, target)
    with (
        patch.object(runner, "TypeSafeClient"),
        patch.object(win32, "activate"),
        patch.object(runner, "observe_desktop", side_effect=[before, after]),
        patch.object(runner, "_choose", return_value=("c0", 1, "none")),
        patch.object(runner, "_perform", return_value="unverifiable") as perform,
    ):
        result = runner.run("Launch", before.window, Provider("test"), delay=0)
    assert result["status"] == "stalled"
    assert not result["goal_achieved"]
    assert result["transitions"][0]["effect"] == "background_change_only"
    assert perform.call_count == 1


def test_uia_status_text_is_proof_but_editable_descendants_are_not():
    from jev_use import uia_cache

    class Control:
        IsOffscreen = False
        IsEnabled = True
        IsPassword = False
        HasKeyboardFocus = False
        BoundingRectangle = SimpleNamespace(left=10, top=10, right=100, bottom=40)

        def __init__(self, role, name, child=None, sibling=None):
            self.ControlTypeName, self.Name = role, name
            self.child, self.sibling = child, sibling

        def GetFirstChildControl(self):
            return self.child

        def GetNextSiblingControl(self):
            return self.sibling

        def GetValuePattern(self):
            return SimpleNamespace(Value="Draft", IsReadOnly=False)

        def GetRuntimeId(self):
            return [1, 2]

    status = Control("TextControl", "Held successfully")
    editor = Control("DocumentControl", "Editor", child=Control("TextControl", "Draft success"), sibling=status)
    root = Control("WindowControl", "Fixture", child=editor)
    proof = []
    with patch.object(uia_cache, "root", return_value=root):
        desktop._uia_targets(1, (0, 0, 400, 240), proof=proof)
    assert proof == ["Held successfully"]


def test_drag_prefers_native_label_over_ocr_duplicate():
    start = desktop.Target("click", "source", (10, 10, 50, 50), "ButtonControl")
    end = desktop.Target("click", "destination", (100, 10, 140, 50), "ButtonControl")
    state = observation(scene())
    state.targets = [start, replace(start, role="OCR"), end, replace(end, role="OCR")]
    actions = runner._actions(state, None, None, {}, None, {"source": "destination"})
    assert actions["drag0"].target == start
    assert actions["drag0"].destination == end


def test_remote_clock_and_subtitles_do_not_allow_duplicate_input():
    target = desktop.Target("click", "launch", (40, 40, 88, 88), "Visual")
    before, after = observation(scene(), target), observation(scene(), target)
    before.text, before.postcondition_text = ["12:00", "old subtitle"], ["12:00"]
    after.text, after.postcondition_text = ["12:01", "new subtitle"], ["12:01"]
    before.targets.append(desktop.Target("click", "12:00", (300, 180, 350, 200), "OCR"))
    after.targets.append(desktop.Target("click", "12:01", (300, 180, 350, 200), "OCR"))
    with (
        patch.object(runner, "TypeSafeClient"),
        patch.object(win32, "activate"),
        patch.object(runner, "observe_desktop", side_effect=[before, after]),
        patch.object(runner, "_choose", return_value=("c0", 1, "none")),
        patch.object(runner, "_perform", return_value="unverifiable") as perform,
    ):
        result = runner.run("Launch", before.window, Provider("test"), delay=0)
    assert result["status"] == "stalled"
    assert not result["goal_achieved"]
    assert perform.call_count == 1


def test_action_state_ignores_movement_but_preserves_control_values():
    target = desktop.Target("click", "launch", (40, 40, 88, 88), "Visual")
    moved = replace(target, rect=(220, 120, 268, 168))
    before, after = observation(scene(), target), observation(scene(220, 120), moved)
    assert runner._snapshot_id(before, target) == runner._snapshot_id(after, moved)
    after.targets.append(desktop.Target("text", "Name", (10, 10, 30, 30), "EditControl", value="new"))
    assert runner._snapshot_id(before, target) != runner._snapshot_id(after, moved)


def test_local_ocr_change_is_retained_for_repeat_guard():
    target = desktop.Target("click", "launch", (40, 40, 88, 88), "Visual")
    before, after = observation(scene(), target), observation(scene(), target)
    after.targets.append(desktop.Target("click", "selected", (40, 90, 88, 110), "OCR"))
    assert runner._snapshot_id(before, target) != runner._snapshot_id(after, target)


@pytest.mark.parametrize("inputs", [{"text": "draft"}, {"url": "https://example.com"}])
def test_generic_done_cannot_skip_pending_input(inputs):
    target = desktop.Target("text", "Message", (40, 40, 88, 88), "EditControl")
    screen = observation(scene(), target)
    screen.postcondition_text = ["Saved"]
    with (
        patch.object(runner, "TypeSafeClient"),
        patch.object(win32, "activate"),
        patch.object(runner, "observe_desktop", return_value=screen),
        patch.object(runner, "_choose", return_value=("done", 1, "p0")),
    ):
        result = runner.run("Save", screen.window, Provider("test"), **inputs)
    assert not result["goal_achieved"]
    assert result["status"] == "not_achieved"


def test_cached_frame_invalidates_on_duplicate_movement_and_removal(tmp_path):
    path = tmp_path / "reference.png"
    icon().save(path)
    refs = {"launch": str(path)}
    visual._frame_cache.last = (None, {})
    with patch.object(visual, "_match_prepared", wraps=visual._match_prepared) as search:
        assert visual.locate_targets(scene(), (0, 0, 400, 240), refs)[0].rect == (40, 40, 88, 88)
        assert visual.locate_targets(scene(), (-400, 0, 0, 240), refs)[0].rect == (-360, 40, -312, 88)
        assert search.call_count == 1
        duplicate = scene()
        duplicate.paste(icon(), (240, 40))
        assert visual.locate_targets(duplicate, (0, 0, 400, 240), refs) == []
        assert visual.locate_targets(scene(220, 120), (0, 0, 400, 240), refs)[0].rect == (220, 120, 268, 168)
        assert visual.locate_targets(Image.new("RGB", (400, 240)), (0, 0, 400, 240), refs) == []
        assert search.call_count == 4


def test_template_changes_invalidate_cached_match(tmp_path):
    path = tmp_path / "reference.png"
    icon().save(path)
    refs = {"launch": str(path)}
    assert visual.locate_targets(scene(), (0, 0, 400, 240), refs)
    Image.new("RGB", (48, 48), "red").save(path)
    # Avoid depending on the filesystem timestamp resolution in this regression.
    visual._load_template.cache_clear()
    assert visual.locate_targets(scene(), (0, 0, 400, 240), refs) == []
