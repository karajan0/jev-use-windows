"""Turn one selected window into compact, local text and action targets."""

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


def observe(window: win32.Window, *, language: str | None = None) -> Observation:
    rect = win32.rect_of(window.handle)
    current = win32.Window(window.handle, window.title, rect)
    image = win32.capture_window(window.handle, rect)
    pending_ocr = _ocr_pool.submit(_ocr_cache.read, image, window.handle, rect, language)
    uia, names = _uia_targets(window.handle, rect)
    words = pending_ocr.result()
    left, top = rect[:2]
    targets = list(uia)

    def covered(word: ocr.TextBox) -> bool:
        x = left + (word.rect[0] + word.rect[2]) // 2
        y = top + (word.rect[1] + word.rect[3]) // 2
        return any(box.rect[0] <= x < box.rect[2] and box.rect[1] <= y < box.rect[3] for box in uia)

    uncovered_words: list[ocr.TextBox] = []
    covered_words: list[ocr.TextBox] = []
    for word in words:
        (covered_words if covered(word) else uncovered_words).append(word)
    for word in [*uncovered_words, *covered_words][:70]:
        box = tuple((value + (left if index % 2 == 0 else top)) for index, value in enumerate(word.rect))
        if _inside(box, rect):
            targets.append(Target("click", word.text, box, "OCR"))
    lines = list(dict.fromkeys(word.line or word.text for word in [*uncovered_words, *covered_words]))
    text = list(dict.fromkeys([*names, *lines]))[:150]
    return Observation(current, image, targets[:140], text, lines)
