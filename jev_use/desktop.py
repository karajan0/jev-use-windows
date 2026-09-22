"""Turn the visible desktop and its foreground window into local action targets."""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass, field

from PIL import Image

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
    """Reuse OCR only when the same window has identical captured pixels."""

    def __init__(self) -> None:
        self.key: tuple[int, tuple[int, int, int, int], str | None] | None = None
        self.digest: bytes | None = None
        self.words: list[ocr.TextBox] = []

    def read(
        self, image: Image.Image, handle: int, rect: tuple[int, int, int, int], language: str | None
    ) -> list[ocr.TextBox]:
        key = (handle, rect, language)
        digest = hashlib.blake2b(image.tobytes(), digest_size=16).digest()
        if key == self.key and digest == self.digest:
            return self.words
        words = ocr.read(image, language)
        self.key, self.digest, self.words = key, digest, words
        return words


_ocr_cache = _OcrFrameCache()


@dataclass(frozen=True)
class Target:
    kind: str
    label: str
    rect: tuple[int, int, int, int]
    role: str
    value: str = ""

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


def _inside(rect: tuple[int, int, int, int], outer: tuple[int, int, int, int]) -> bool:
    left, top, right, bottom = rect
    return (
        right > left
        and bottom > top
        and outer[0] <= (left + right) // 2 < outer[2]
        and outer[1] <= (top + bottom) // 2 < outer[3]
    )


def _uia_targets(handle: int, window_rect: tuple[int, int, int, int]) -> tuple[list[Target], list[str]]:
    import uiautomation as auto

    root = auto.ControlFromHandle(handle)
    stack = [(root, 0)]
    found: list[Target] = []
    text: list[str] = []
    seen = 0
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
                    role in TEXT_ROLES and (control.IsPassword or "password" in name.casefold())
                ):
                    value = ""
                    if role in TEXT_ROLES:
                        with suppress(AttributeError, RuntimeError, OSError):
                            value = str(control.GetValuePattern().Value or "")[:160]
                        if value and len(text) < 120:
                            text.append(f"{name or role}: {value}")
                    found.append(Target("text" if role in TEXT_ROLES else "click", name or role, rect, role, value))
            if depth < 5:
                child = control.GetFirstChildControl()
                if child:
                    stack.append((child, depth + 1))
        except (AttributeError, RuntimeError, OSError):
            continue
    return found, text


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
    return Observation(window, image, targets[:140], text, lines, bounds)


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
