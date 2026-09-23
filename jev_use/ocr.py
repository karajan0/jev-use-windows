"""Read screen text with the Windows OCR engine; no image leaves the machine."""

from __future__ import annotations

import asyncio
import hashlib
from collections import OrderedDict
from dataclasses import dataclass
from threading import local

from PIL import Image

from .metrics import timed

_worker = local()


def _tile_read(image, language):
    """Bounded, thread-owned exact-pixel cache; no screenshots are retained."""
    if not hasattr(_worker, "tiles"):
        _worker.tiles = OrderedDict()
    key = (image.size, image.mode, language, hashlib.blake2s(image.tobytes()).digest())
    cached = _worker.tiles.get(key)
    if cached is not None:
        _worker.tiles.move_to_end(key)
        return cached
    enlarged = image.resize((image.width * 2, image.height * 2), Image.Resampling.LANCZOS)
    words = _worker.runner.run(_read(enlarged, language))
    _worker.tiles[key] = words
    if len(_worker.tiles) > 64:
        _worker.tiles.popitem(last=False)
    return words


@dataclass(frozen=True)
class TextBox:
    text: str
    rect: tuple[int, int, int, int]
    line: str = ""


def available_languages() -> list[str]:
    from winrt.windows.media.ocr import OcrEngine

    return [item.language_tag for item in OcrEngine.available_recognizer_languages]


def _engine(language: str | None):
    from winrt.windows.globalization import Language
    from winrt.windows.media.ocr import OcrEngine

    if not hasattr(_worker, "engines"):
        _worker.engines = {}
    if language in _worker.engines:
        return _worker.engines[language]
    engine = (
        OcrEngine.try_create_from_language(Language(language))
        if language
        else OcrEngine.try_create_from_user_profile_languages()
    )
    if engine is None:
        raise RuntimeError("Windows OCR has no usable language pack")
    _worker.engines[language] = engine
    return engine


async def _read(image: Image.Image, language: str | None) -> list[TextBox]:
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.storage.streams import DataWriter

    engine = _engine(language)
    image = image.convert("RGBA")
    scale = min(1.0, 1800 / max(image.size))
    if scale < 1:
        image = image.resize((round(image.width * scale), round(image.height * scale)))
    writer = DataWriter()
    writer.write_bytes(image.tobytes())
    bitmap = SoftwareBitmap(BitmapPixelFormat.RGBA8, image.width, image.height)
    bitmap.copy_from_buffer(writer.detach_buffer())
    result = await engine.recognize_async(bitmap)
    boxes: list[TextBox] = []
    for line in result.lines:
        line_text = " ".join(word.text.strip() for word in line.words if word.text.strip())
        for word in line.words:
            bounds = word.bounding_rect
            x1 = round(bounds.x / scale)
            y1 = round(bounds.y / scale)
            x2 = round((bounds.x + bounds.width) / scale)
            y2 = round((bounds.y + bounds.height) / scale)
            if word.text.strip():
                boxes.append(TextBox(word.text.strip(), (x1, y1, x2, y2), line_text))
    return boxes


@timed("ocr")
def read(image: Image.Image, language: str | None = None) -> list[TextBox]:
    if not hasattr(_worker, "runner"):
        _worker.runner = asyncio.Runner()
    if max(image.size) <= 1800:
        return _worker.runner.run(_read(image, language))
    # Preserve tiny UI lettering. Overlapping tiles are enlarged for OCR, never
    # shrunk as a whole desktop; each word belongs to exactly one tile core.
    words = []
    core, margin, magnification = 768, 48, 2
    for top in range(0, image.height, core):
        for left in range(0, image.width, core):
            x, y = max(0, left - margin), max(0, top - margin)
            right, bottom = min(image.width, left + core + margin), min(image.height, top + core + margin)
            crop = image.crop((x, y, right, bottom))
            for word in _tile_read(crop, language):
                rect = tuple(
                    round(value / magnification) + (x if index % 2 == 0 else y) for index, value in enumerate(word.rect)
                )
                cx, cy = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
                if left <= cx < min(left + core, image.width) and top <= cy < min(top + core, image.height):
                    words.append(TextBox(word.text, rect, word.line))
    return sorted(words, key=lambda word: (word.rect[1], word.rect[0]))


def close_worker() -> None:
    """Release resources on the same thread that created the OCR engine."""
    if hasattr(_worker, "runner"):
        _worker.runner.close()
        del _worker.runner
    if hasattr(_worker, "engines"):
        del _worker.engines
    if hasattr(_worker, "tiles"):
        del _worker.tiles
