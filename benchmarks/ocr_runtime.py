"""Compare OCR implementations on generated text without observing the desktop."""

import argparse
import importlib.util
import json
import statistics
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from jev_use import ocr


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=12)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("baseline_ocr", args.baseline)
    baseline = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = baseline
    spec.loader.exec_module(baseline)
    image = Image.new("RGB", (1000, 400), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 32)
    draw.text((30, 30), "Saved successfully\nName: Alice\nOrder number 12345", font=font, fill="black", spacing=20)
    readings = {"baseline": [], "updated": []}
    expected = None
    for index in range(args.iterations + 1):
        variants = [("baseline", baseline.read), ("updated", ocr.read)]
        if index % 2:
            variants.reverse()
        for name, read in variants:
            started = time.perf_counter()
            words = read(image)
            elapsed = time.perf_counter() - started
            text = " ".join(word.text for word in words)
            if expected is None:
                expected = text
            if text != expected or "12345" not in text:
                raise RuntimeError("OCR output changed or fixture was not recognized")
            if index:
                readings[name].append(elapsed)
    ocr.close_worker()
    result = {
        name: {"median_ms": round(statistics.median(samples) * 1000, 3), "samples": len(samples)}
        for name, samples in readings.items()
    }
    result["same_text"] = True
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
