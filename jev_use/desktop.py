"""Turn the visible desktop and its foreground window into local action targets."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass, field

from PIL import Image, ImageChops

from . import ocr, win32

CLICK_ROLES = {
    "ButtonControl",
    "CheckBoxControl",
    "ComboBoxControl",
    "HyperlinkControl",
    "ListItemControl",
    "MenuItemControl",
    "RadioButtonControl",
    "TabItemControl",
}
TEXT_ROLES = {"EditControl", "DocumentControl"}
_ocr_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jev-ocr")


class _OcrFrameCache:
    """Reuse unchanged text and reread only horizontal bands that changed."""

    def __init__(self) -> None:
        self.key: tuple[int, tuple[int, int, int, int], str | None] | None = None
        self.image: Image.Image | None = None
        self.words: list[ocr.TextBox] = []

    def read(
        self, image: Image.Image, handle: int, rect: tuple[int, int, int, int], language: str | None
    ) -> list[ocr.TextBox]:
        key = (handle, rect, language)
        if self.key != key or self.image is None or self.image.size != image.size:
            words = ocr.read(image, language)
        else:
            changed = ImageChops.difference(self.image, image)
            if changed.getbbox() is None:
                return self.words
            height = image.height
            band_height = 128
            rows = [
                row
                for row in range(0, height, band_height)
                if changed.crop((0, row, image.width, min(row + band_height, height))).getbbox() is not None
            ]
            bands: list[tuple[int, int]] = []
            for row in rows:
                top, bottom = max(0, row - 32), min(height, row + band_height + 32)
                if bands and top <= bands[-1][1]:
                    bands[-1] = (bands[-1][0], max(bands[-1][1], bottom))
                else:
                    bands.append((top, bottom))
            if len(bands) > 3 or sum(bottom - top for top, bottom in bands) > height * 0.55:
                words = ocr.read(image, language)
            else:
                words = [
                    word
                    for word in self.words
                    if all(word.rect[3] <= top or word.rect[1] >= bottom for top, bottom in bands)
                ]
                for top, bottom in bands:
                    crop = image.crop((0, top, image.width, bottom))
                    words.extend(
                        ocr.TextBox(
                            word.text, (word.rect[0], word.rect[1] + top, word.rect[2], word.rect[3] + top), word.line
                        )
                        for word in ocr.read(crop, language)
                    )
                words.sort(key=lambda word: (word.rect[1], word.rect[0]))
        self.key, self.image, self.words = key, image.copy(), words
        return words


_ocr_cache = _OcrFrameCache()


@dataclass(frozen=True)
class Target:
    kind: str
    label: str
    rect: tuple[int, int, int, int]
    role: str
    value: str = ""
    focused: bool = False

    @property
    def center(self) -> tuple[int, int]:
        left, top, right, bottom = self.rect
        return ((left + right) // 2, (top + bottom) // 2)


@dataclass
class Observation:
    window: win32.Window
    image: Image.Image
    targets: list[Target]
    text: list[str]
    ocr_lines: list[str] = field(default_factory=list)
    bounds: tuple[int, int, int, int] | None = None
    postcondition_text: list[str] = field(default_factory=list)


def _inside(rect: tuple[int, int, int, int], outer: tuple[int, int, int, int]) -> bool:
    left, top, right, bottom = rect
    return (
        right > left
        and bottom > top
        and outer[0] <= (left + right) // 2 < outer[2]
        and outer[1] <= (top + bottom) // 2 < outer[3]
    )


def _visual_phrases(words: list[ocr.TextBox]) -> list[str]:
    """Join nearby OCR words even when the OCR engine splits one visible row."""
    rows: list[list[ocr.TextBox]] = []
    for word in sorted(words, key=lambda item: ((item.rect[1] + item.rect[3]) // 2, item.rect[0])):
        center = (word.rect[1] + word.rect[3]) // 2
        height = word.rect[3] - word.rect[1]
        row = next(
            (
                item
                for item in reversed(rows[-4:])
                if abs(center - (item[0].rect[1] + item[0].rect[3]) // 2)
                <= max(6, min(height, item[0].rect[3] - item[0].rect[1]) * 0.6)
            ),
            None,
        )
        if row is None:
            rows.append([word])
        else:
            row.append(word)
    phrases: list[str] = []
    for row in rows:
        group: list[ocr.TextBox] = []
        for word in sorted(row, key=lambda item: item.rect[0]):
            if group and word.rect[0] - group[-1].rect[2] > max(
                24, 2 * max(word.rect[3] - word.rect[1], group[-1].rect[3] - group[-1].rect[1])
            ):
                phrases.append(" ".join(item.text for item in group))
                group = []
            group.append(word)
        if group:
            phrases.append(" ".join(item.text for item in group))
    return list(dict.fromkeys(phrases))


def _uia_targets(handle: int, window_rect: tuple[int, int, int, int]) -> tuple[list[Target], list[str]]:
    import uiautomation as auto
    from comtypes import COMError

    root = auto.ControlFromHandle(handle)
    stack = [(root, 0)]
    found: list[Target] = []
    text: list[str] = []
    seen = 0
    focused_point: tuple[int, int] | None = None
    with suppress(AttributeError, RuntimeError, OSError, COMError):
        focused_control = auto.GetFocusedControl()
        if focused_control is not None:
            focus_box = focused_control.BoundingRectangle
            focused_point = (
                (int(focus_box.left) + int(focus_box.right)) // 2,
                (int(focus_box.top) + int(focus_box.bottom)) // 2,
            )
    while stack and seen < 350 and len(found) < 80:
        control, depth = stack.pop()
        seen += 1
        try:
            # Schedule the sibling first, then the child, to keep depth-first
            # order without enumerating every sibling of a dense panel up front.
            if depth:
                sibling = control.GetNextSiblingControl()
                if sibling:
                    stack.append((sibling, depth))
            role = str(control.ControlTypeName)
            name = str(control.Name or "").strip()[:160]
            if name and name not in text and len(text) < 120:
                text.append(name)
            if role in CLICK_ROLES | TEXT_ROLES and not control.IsOffscreen and control.IsEnabled:
                box = control.BoundingRectangle
                rect = (int(box.left), int(box.top), int(box.right), int(box.bottom))
                if _inside(rect, window_rect) and not (
                    role in TEXT_ROLES | {"ComboBoxControl"} and (control.IsPassword or "password" in name.casefold())
                ):
                    value = ""
                    editable = role in TEXT_ROLES
                    if role in TEXT_ROLES | {"ComboBoxControl"}:
                        with suppress(AttributeError, RuntimeError, OSError, COMError):
                            pattern = control.GetValuePattern()
                            value = str(pattern.Value or "")[:160]
                            if role == "ComboBoxControl":
                                editable = not pattern.IsReadOnly
                        if value and len(text) < 120:
                            text.append(f"{name or role}: {value}")
                    focused = bool(
                        focused_point
                        and rect[0] <= focused_point[0] < rect[2]
                        and rect[1] <= focused_point[1] < rect[3]
                    )
                    found.append(Target("text" if editable else "click", name or role, rect, role, value, focused))
            if depth < 5:
                child = control.GetFirstChildControl()
                if child:
                    stack.append((child, depth + 1))
        except (AttributeError, RuntimeError, OSError, COMError):
            continue
    return found, text


def read_focused_value(target: Target) -> str | None:
    """Read back the field that received text, when UI Automation exposes a value."""
    import uiautomation as auto
    from comtypes import COMError

    with suppress(AttributeError, RuntimeError, OSError, COMError):
        control = auto.GetFocusedControl()
        if control is None or control.IsPassword:
            return None
        box = control.BoundingRectangle
        center = ((int(box.left) + int(box.right)) // 2, (int(box.top) + int(box.bottom)) // 2)
        if not (target.rect[0] <= center[0] < target.rect[2] and target.rect[1] <= center[1] < target.rect[3]):
            return None
        return str(control.GetValuePattern().Value or "")
    return None


def invoke_target(target: Target) -> bool:
    """Invoke a matching native control; return False when pixel input is needed."""
    if target.role not in {"ButtonControl", "HyperlinkControl", "MenuItemControl"}:
        return False
    import uiautomation as auto
    from comtypes import COMError

    try:
        control = auto.ControlFromPoint(*target.center)
        if control is None or control.ControlTypeName != target.role:
            return False
        if str(control.Name or "").strip()[:160] != target.label:
            return False
        pattern = control.GetInvokePattern()
        if pattern is None:
            return False
    except (AttributeError, RuntimeError, OSError, COMError):
        return False
    if not pattern.Invoke(waitTime=0):
        raise RuntimeError("UI Automation could not confirm whether the control was invoked")
    return True


def set_combo_value(target: Target, value: str) -> str | None:
    """Set an editable combo box directly, or return None for pixel fallback."""
    if target.role != "ComboBoxControl":
        return None
    import uiautomation as auto
    from comtypes import COMError

    try:
        control = auto.ControlFromPoint(*target.center)
        for _ in range(4):
            if control is None:
                return None
            if control.ControlTypeName == target.role and str(control.Name or "").strip()[:160] == target.label:
                pattern = control.GetValuePattern()
                if pattern is None or pattern.IsReadOnly:
                    return None
                break
            control = control.GetParentControl()
        else:
            return None
    except (AttributeError, RuntimeError, OSError, COMError):
        return None
    if not pattern.SetValue(value, waitTime=0):
        raise RuntimeError("UI Automation could not confirm whether the field was changed")
    return str(pattern.Value or "")


def observe_controls(window: win32.Window) -> tuple[win32.Window, list[Target]]:
    """Read stable UI Automation controls without paying for capture or OCR."""
    rect = win32.rect_of(window.handle)
    current = win32.Window(window.handle, window.title, rect)
    targets, _ = _uia_targets(window.handle, rect)
    return current, targets


def _finish_observation(
    window: win32.Window,
    image: Image.Image,
    ocr_image: Image.Image,
    ocr_rect: tuple[int, int, int, int],
    bounds: tuple[int, int, int, int],
    language: str | None,
) -> Observation:
    pending_ocr = _ocr_pool.submit(_ocr_cache.read, ocr_image, window.handle, ocr_rect, language)
    uia, names = _uia_targets(window.handle, window.rect)
    words = pending_ocr.result()
    left, top = ocr_rect[:2]

    def covered(word: ocr.TextBox) -> bool:
        x = left + (word.rect[0] + word.rect[2]) // 2
        y = top + (word.rect[1] + word.rect[3]) // 2
        return any(box.rect[0] <= x < box.rect[2] and box.rect[1] <= y < box.rect[3] for box in uia)

    uncovered = [word for word in words if not covered(word)]
    covered_words = [word for word in words if covered(word)]
    targets = list(uia)
    for word in [*uncovered, *covered_words][:70]:
        box = (left + word.rect[0], top + word.rect[1], left + word.rect[2], top + word.rect[3])
        if _inside(box, window.rect):
            targets.append(Target("click", word.text, box, "OCR"))
    lines = list(dict.fromkeys(word.line or word.text for word in [*uncovered, *covered_words]))
    text = list(dict.fromkeys([window.title, *names, *lines]))[:150]
    editable = [target.rect for target in uia if target.kind == "text"]

    def in_edit(word: ocr.TextBox) -> bool:
        x = left + (word.rect[0] + word.rect[2]) // 2
        y = top + (word.rect[1] + word.rect[3]) // 2
        return any(rect[0] <= x < rect[2] and rect[1] <= y < rect[3] for rect in editable)

    postcondition_text = _visual_phrases([word for word in words if not in_edit(word)])[:150]
    return Observation(window, image, targets[:140], text, lines, bounds, postcondition_text)


def observe(window: win32.Window, *, language: str | None = None) -> Observation:
    rect = win32.rect_of(window.handle)
    current = win32.Window(window.handle, window.title, rect)
    image = win32.capture_window(window.handle, rect)
    return _finish_observation(current, image, image, rect, rect, language)


def observe_desktop(*, language: str | None = None) -> Observation:
    """Read pixels from the live desktop and controls from its current foreground window."""
    for _ in range(2):
        before = win32.select_window(None)
        bounds, image = win32.capture_desktop()
        foreground = win32.select_window(None)
        if before.handle == foreground.handle:
            break
    else:
        raise RuntimeError("The foreground window changed during capture")

    crop = (
        max(bounds[0], foreground.rect[0]),
        max(bounds[1], foreground.rect[1]),
        min(bounds[2], foreground.rect[2]),
        min(bounds[3], foreground.rect[3]),
    )
    if crop[2] <= crop[0] or crop[3] <= crop[1]:
        raise RuntimeError("The foreground window is outside the desktop")
    foreground_image = image.crop((crop[0] - bounds[0], crop[1] - bounds[1], crop[2] - bounds[0], crop[3] - bounds[1]))
    return _finish_observation(foreground, image, foreground_image, crop, bounds, language)
