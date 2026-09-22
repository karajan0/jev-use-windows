"""Read screen text with the Windows OCR engine; no image leaves the machine."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from PIL import Image


@dataclass(frozen=True)
class TextBox:
    text: str
    rect: tuple[int, int, int, int]
    line: str = ""


def available_languages() -> list[str]:
    from winrt.windows.media.ocr import OcrEngine

    return [item.language_tag for item in OcrEngine.available_recognizer_languages]


async def _read(image: Image.Image, language: str | None) -> list[TextBox]:
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter

    engine = (
        OcrEngine.try_create_from_language(Language(language))
        if language
        else OcrEngine.try_create_from_user_profile_languages()
    )
    if engine is None:
        raise RuntimeError("Windows OCR has no usable language pack")
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


def read(image: Image.Image, language: str | None = None) -> list[TextBox]:
    return asyncio.run(_read(image, language))
