from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw

from jev_use import desktop, grounding, ocr, runner, win32


def test_late_screen_candidate_survives_goal_budget():
    targets = [desktop.Target("click", f"Other {i}", (0, i * 20, 80, i * 20 + 16), "OCR") for i in range(300)]
    targets.append(desktop.Target("click", "Export", (1200, 900, 1280, 920), "OCR"))
    screen = desktop.Observation(win32.Window(1, "Test", (0, 0, 1600, 6500)), Image.new("RGB", (1, 1)), targets, [])
    actions = runner._actions(screen, None, None, {}, None)
    selected = grounding.select_actions("Open the Export menu", actions)
    assert "c300" in selected
    assert sum(action.target is not None for action in selected.values()) <= 140
    assert "done" in selected and "needs_input" in selected
    assert len({action.target.center[1] // 1000 for action in selected.values() if action.target}) >= 4


def test_visual_proposals_exclude_text_and_preserve_origin():
    image = Image.new("RGB", (200, 100), "white")
    draw = ImageDraw.Draw(image)
    draw.line((22, 23, 32, 33), fill="black", width=2)
    draw.line((22, 33, 32, 23), fill="black", width=2)
    draw.rectangle((50, 20, 90, 36), fill="black")
    words = [ocr.TextBox("Document", (50, 20, 90, 36), "Document")]
    targets = grounding.visual_controls(image, words, (-300, 40, -100, 140))
    assert len(targets) == 1
    assert -280 <= targets[0].center[0] <= -265
    assert "left of 'Document'" in targets[0].context


def test_tiled_ocr_owns_boundary_words_once():
    calls = []

    async def recognize(image, language):
        calls.append(image.size)
        if len(calls) == 1:
            return [ocr.TextBox("Boundary", (1520, 40, 1560, 60))]
        if len(calls) == 2:
            return [ocr.TextBox("Boundary", (80, 40, 120, 60))]
        return []

    ocr.close_worker()
    try:
        with patch.object(ocr, "_read", side_effect=recognize):
            result = ocr.read(Image.new("RGB", (2000, 200)), "en-US")
        assert len(result) == 1
        assert result[0].rect == (760, 20, 780, 30)
        assert max(max(size) for size in calls) <= 1800
    finally:
        ocr.close_worker()


def test_single_action_choice_has_no_cross_question_dependency():
    target = desktop.Target("click", "File", (0, 0, 40, 20), "OCR")
    screen = desktop.Observation(win32.Window(1, "Test", (0, 0, 100, 100)), Image.new("RGB", (100, 100)), [target], [])
    response = SimpleNamespace(
        answers={
            "action": SimpleNamespace(choice="c0", confidence=0.9),
            "proof": SimpleNamespace(choice="none", confidence=0.9),
        }
    )
    from unittest.mock import Mock

    client = Mock()
    client.system_one.return_value = response
    selected = runner._choose(client, "Open File", screen, runner._actions(screen, None, None, {}, None), [])
    assert selected == ("c0", 0.9, "none")
    assert set(client.system_one.call_args.kwargs["questions"]) == {"action", "proof"}


def test_observation_retains_ocr_past_old_limit():
    words = [ocr.TextBox(f"Word{i}", (10, i * 20, 60, i * 20 + 16)) for i in range(100)]
    image = Image.new("RGB", (100, 2100))
    window = win32.Window(1, "Test", (0, 0, 100, 2100))
    with (
        patch.object(desktop, "_uia_targets", return_value=([], [])),
        patch.object(desktop._ocr_cache, "read", return_value=words),
    ):
        result = desktop._finish_observation(window, image, image, window.rect, window.rect, "en-US")
    assert any(target.label == "Word99" for target in result.targets)


def test_glyph_descriptor_recognizes_shape_without_reference_image():
    image = Image.new("L", (30, 30), "white")
    draw = ImageDraw.Draw(image)
    draw.line((5, 5, 24, 24), fill="black", width=3)
    draw.line((24, 5, 5, 24), fill="black", width=3)
    assert grounding.describe_glyph(np.asarray(image)) == "X-shaped close icon"
    assert grounding.describe_glyph(np.full((30, 30), 255, dtype=np.uint8)) == "Unlabeled visual control"


def test_spatial_context_distinguishes_same_glyph_in_different_rows():
    words = [ocr.TextBox("Alpha", (40, 10, 90, 30)), ocr.TextBox("Beta", (40, 80, 90, 100))]
    assert grounding.spatial_context((10, 10, 20, 30), words).startswith("left of 'Alpha'")
    assert grounding.spatial_context((10, 80, 20, 100), words).startswith("left of 'Beta'")
