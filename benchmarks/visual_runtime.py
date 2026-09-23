"""Synthetic regression benchmark for moving/scaled icons amid changing backgrounds."""

import json
import statistics
import time
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

from jev_use.visual import unique_match


def make_icon(drop=False):
    image = Image.new("RGB", (48, 48), (20, 45, 85))
    draw = ImageDraw.Draw(image)
    draw.rectangle((2, 2, 45, 45), outline="white", width=2)
    if drop:
        draw.ellipse((10, 10, 38, 38), outline="orange", width=5)
    else:
        draw.polygon([(13, 10), (36, 24), (13, 38)], fill=(40, 230, 170))
    draw.rectangle((5, 5, 9, 9), fill="orange")
    return image


def main():
    root = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
    template = make_icon()
    for name, asset in (("launch.png", template), ("dropzone.png", make_icon(True))):
        path = root / name
        if path.exists():
            with Image.open(path) as existing:
                if (
                    existing.size == asset.size
                    and ImageChops.difference(existing.convert("RGB"), asset).getbbox() is None
                ):
                    continue
        asset.save(path)
    elapsed = []
    correct = 0
    for index in range(24):
        image = Image.new("RGB", (800, 500), (15, 15, 25))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 340, 799, 499), fill=(index * 10, 80, 160))
        size = [36, 48, 60, 72][index % 4]
        x, y = 30 + index * 23, 60 + index % 3 * 50
        resized = template.resize((size, size), Image.Resampling.BILINEAR)
        image.paste(resized, (x, y))
        duplicate = index % 6 == 0
        if duplicate:
            image.paste(resized, (700, 240))
        started = time.perf_counter()
        found = unique_match(image, template)
        elapsed.append(time.perf_counter() - started)
        correct += (found is None) if duplicate else bool(found and found.rect == (x, y, x + size, y + size))
    report = {
        "cases": 24,
        "correct": correct,
        "duplicate_rejections": 4,
        "median_ms": round(statistics.median(elapsed) * 1000, 3),
        "max_ms": round(max(elapsed) * 1000, 3),
    }
    print(json.dumps(report, indent=2))
    if correct != 24:
        raise RuntimeError("Visual matching benchmark failed")


if __name__ == "__main__":
    main()
