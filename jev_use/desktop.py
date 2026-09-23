"""Turn the visible desktop and its foreground window into local action targets."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from contextvars import copy_context
from dataclasses import dataclass, field

from PIL import Image, ImageChops

from . import ocr, win32
from .metrics import timed

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
    runtime_id: tuple[int, ...] = ()
    context: str = ""

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


@timed("uia")
def _uia_targets(
    handle: int,
    window_rect: tuple[int, int, int, int],
    *,
    cached: bool = True,
    proof: list[str] | None = None,
    target_labels=(),
    expanded=False,
) -> tuple[list[Target], list[str]]:
    import uiautomation as auto
    from comtypes import COMError

    from . import uia_cache

    try:
        root = uia_cache.root(handle) if cached else auto.ControlFromHandle(handle)
    except (AttributeError, RuntimeError, OSError, COMError):
        root = auto.ControlFromHandle(handle)
    stack = [(root, 0, False)]
    found: list[Target] = []
    text: list[str] = []
    seen = 0
    limit, depth_limit = (2000, 12) if expanded else (350, 5)
    truncated = False
    proof_start = len(proof) if proof is not None else 0
    while stack and seen < limit:
        control, depth, editable_ancestor = stack.pop()
        seen += 1
        try:
            # Schedule the sibling first, then the child, to keep depth-first
            # order without enumerating every sibling of a dense panel up front.
            if depth:
                sibling = control.GetNextSiblingControl()
                if sibling:
                    stack.append((sibling, depth, editable_ancestor))
            role = str(control.ControlTypeName)
            name = str(control.Name or "").strip()[:160]
            if name and name not in text and len(text) < 120:
                text.append(name)
            if (
                proof is not None
                and name
                and not editable_ancestor
                and role in {"TextControl", "StatusBarControl"}
                and not control.IsOffscreen
            ):
                box = control.BoundingRectangle
                if _inside((int(box.left), int(box.top), int(box.right), int(box.bottom)), window_rect):
                    proof.append(name)
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
                    focused = bool(control.HasKeyboardFocus)
                    runtime_id = tuple(control.GetRuntimeId())
                    found.append(
                        Target("text" if editable else "click", name or role, rect, role, value, focused, runtime_id)
                    )
            child = control.GetFirstChildControl()
            if child:
                if depth < depth_limit:
                    stack.append((child, depth + 1, editable_ancestor or role in TEXT_ROLES | {"ComboBoxControl"}))
                else:
                    truncated = True
        except (AttributeError, RuntimeError, OSError, COMError):
            continue
    truncated = truncated or bool(stack)
    if truncated and target_labels:
        if expanded:
            raise RuntimeError("Target search exceeded its tree budget; target uniqueness cannot be verified")
        if proof is not None:
            del proof[proof_start:]
        return _uia_targets(handle, window_rect, cached=cached, proof=proof, target_labels=target_labels, expanded=True)
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


def _matching_control(target: Target):
    """Resolve a live UI Automation element at the observed target point."""
    if target.role in {"OCR", "Visual"}:
        return None
    import uiautomation as auto
    from comtypes import COMError

    try:
        control = auto.ControlFromPoint(*target.center)
        for _ in range(4):
            if control is None:
                return None
            if control.ControlTypeName == target.role and str(control.Name or "").strip()[:160] == target.label:
                box = control.BoundingRectangle
                rect = (int(box.left), int(box.top), int(box.right), int(box.bottom))
                if rect != target.rect or control.IsOffscreen or not control.IsEnabled:
                    return None
                if target.runtime_id and tuple(control.GetRuntimeId()) != target.runtime_id:
                    return None
                return control
            control = control.GetParentControl()
    except (AttributeError, RuntimeError, OSError, COMError):
        return None
    return None


def target_matches(target: Target) -> bool:
    return _matching_control(target) is not None


@timed("native_text")
def set_target_value(target: Target, value: str) -> bool | None:
    """Use a native writable value provider; never retry a dispatched failure."""
    if target.role not in {"EditControl", "ComboBoxControl"} or not target.runtime_id:
        return None
    control = _matching_control(target)
    if control is None:
        return None
    if control.IsPassword:
        raise RuntimeError("The field became a protected input")
    try:
        pattern = control.GetValuePattern()
        if pattern is None:
            return None
        if pattern.IsReadOnly:
            raise ValueError("The field is read-only")
    except AttributeError:
        return None
    if not pattern.SetValue(value, waitTime=0):
        raise RuntimeError("Native text input was dispatched but could not be confirmed")
    deadline = time.monotonic() + 0.4
    while True:
        if str(pattern.Value or "") == value:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.01)


def invoke_target(target: Target) -> bool:
    """Invoke a matching native control; return False when pixel input is needed."""
    if target.role not in {"ButtonControl", "HyperlinkControl", "MenuItemControl"}:
        return False
    from comtypes import COMError

    control = _matching_control(target)
    if control is None:
        return False
    try:
        pattern = control.GetInvokePattern()
        if pattern is None:
            return False
    except (AttributeError, RuntimeError, OSError, COMError):
        return False
    if not pattern.Invoke(waitTime=0):
        raise RuntimeError("UI Automation could not confirm whether the control was invoked")
    return True


def observe_controls(window: win32.Window, *, target_labels=()) -> tuple[win32.Window, list[Target]]:
    """Read stable UI Automation controls without paying for capture or OCR."""
    rect = win32.rect_of(window.handle)
    current = win32.Window(window.handle, window.title, rect)
    targets, _ = _uia_targets(window.handle, rect, target_labels=target_labels)
    return current, targets


def _finish_observation(
    window: win32.Window,
    image: Image.Image,
    ocr_image: Image.Image,
    ocr_rect: tuple[int, int, int, int],
    bounds: tuple[int, int, int, int],
    language: str | None,
    visual_targets: dict[str, str] | None = None,
    target_labels=(),
) -> Observation:
    pending_ocr = _ocr_pool.submit(copy_context().run, _ocr_cache.read, ocr_image, window.handle, ocr_rect, language)
    uia_proof: list[str] = []
    uia, names = _uia_targets(window.handle, window.rect, proof=uia_proof, target_labels=target_labels)
    words = pending_ocr.result()
    left, top = ocr_rect[:2]

    def covered(word: ocr.TextBox) -> bool:
        x = left + (word.rect[0] + word.rect[2]) // 2
        y = top + (word.rect[1] + word.rect[3]) // 2
        return any(box.rect[0] <= x < box.rect[2] and box.rect[1] <= y < box.rect[3] for box in uia)

    uncovered = [word for word in words if not covered(word)]
    covered_words = [word for word in words if covered(word)]
    targets = list(uia)
    if visual_targets:
        from .visual import locate_targets

        targets.extend(locate_targets(ocr_image, ocr_rect, visual_targets))
    for word in [*uncovered, *covered_words]:
        box = (left + word.rect[0], top + word.rect[1], left + word.rect[2], top + word.rect[3])
        if _inside(box, window.rect):
            context = word.line
            if len(word.text) <= 2:
                from .grounding import spatial_context

                context = spatial_context(word.rect, words) or context
            targets.append(Target("click", word.text, box, "OCR", context=context))
    # Dense custom-drawn windows may expose no native controls. Retain geometry
    # and neighboring labels for small non-text controls instead of dropping them.
    if len(uia) < 8 and words:
        from .grounding import visual_controls

        targets.extend(visual_controls(ocr_image, words, ocr_rect))
    lines = list(dict.fromkeys(word.line or word.text for word in [*uncovered, *covered_words]))
    text = list(dict.fromkeys([window.title, *names, *lines]))[:150]
    editable = [target.rect for target in uia if target.kind == "text"]

    def in_edit(word: ocr.TextBox) -> bool:
        x = left + (word.rect[0] + word.rect[2]) // 2
        y = top + (word.rect[1] + word.rect[3]) // 2
        return any(rect[0] <= x < rect[2] and rect[1] <= y < rect[3] for rect in editable)

    postcondition_text = list(
        dict.fromkeys([*uia_proof, *_visual_phrases([word for word in words if not in_edit(word)])])
    )[:150]
    return Observation(window, image, targets, text, lines, bounds, postcondition_text)


def observe(window: win32.Window, *, language: str | None = None, visual_targets=None) -> Observation:
    rect = win32.rect_of(window.handle)
    current = win32.Window(window.handle, window.title, rect)
    image = win32.capture_window(window.handle, rect)
    return _finish_observation(current, image, image, rect, rect, language, visual_targets)


@timed("observation")
def observe_desktop(*, language: str | None = None, visual_targets=None, target_labels=()) -> Observation:
    """Read pixels from the live desktop and controls from its current foreground window."""
    for _ in range(2):
        before = win32.select_window(None)
        desktop = win32.desktop_bounds()
        bounds = (
            max(desktop[0], before.rect[0]),
            max(desktop[1], before.rect[1]),
            min(desktop[2], before.rect[2]),
            min(desktop[3], before.rect[3]),
        )
        if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
            raise RuntimeError("The foreground window is outside the desktop")
        image = win32.capture_region(bounds)
        foreground = win32.select_window(None)
        if before.handle == foreground.handle and before.rect == foreground.rect:
            break
    else:
        raise RuntimeError("The foreground window changed during capture")

    result = _finish_observation(foreground, image, image, bounds, bounds, language, visual_targets, target_labels)
    after = win32.select_window(None)
    if after.handle != foreground.handle or after.rect != foreground.rect:
        raise RuntimeError("The foreground window changed during observation; observe again")
    return result


@timed("settle")
def wait_for_update(screen: Observation, timeout: float = 0.3, poll: float = 0.025, region=None) -> bool:
    """Wait cheaply for changed pixels or foreground; no OCR or model calls."""
    deadline = time.monotonic() + timeout
    bounds = screen.bounds or screen.window.rect
    reference = screen.image
    if region:
        bounds = (
            max(bounds[0], region[0] - 24),
            max(bounds[1], region[1] - 24),
            min(bounds[2], region[2] + 24),
            min(bounds[3], region[3] + 24),
        )
        origin = screen.bounds or screen.window.rect
        reference = screen.image.crop(tuple(value - origin[index % 2] for index, value in enumerate(bounds)))
    previous = None
    while True:
        current = win32.select_window(None)
        if current.handle != screen.window.handle or current.rect != screen.window.rect:
            return True
        image = win32.capture_region(bounds)
        if image.size != reference.size or ImageChops.difference(image, reference).getbbox():
            if region is None:
                return True
            from .visual import changed_fraction

            if changed_fraction(reference, image) >= 0.02:
                if previous is not None and changed_fraction(previous, image) < 0.01:
                    return True
                previous = image
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(poll, remaining))
