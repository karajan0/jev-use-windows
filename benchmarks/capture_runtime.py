"""Compare live capture backends on the static portion of the test fixture."""

import json
import statistics
import time

from PIL import ImageChops, ImageGrab

from jev_use import win32


def main():
    win32.enable_dpi_awareness()
    window = win32.select_window("Jev Regression Fixture")
    left, top, right, bottom = window.rect
    rect = (left + 20, top + 170, right - 20, bottom - 40)
    samples = {"pillow": [], "region_gdi": []}
    mismatch = 0
    for i in range(13):
        images = {}
        order = ["pillow", "region_gdi"] if i % 2 else ["region_gdi", "pillow"]
        for name in order:
            started = time.perf_counter()
            images[name] = (
                ImageGrab.grab(bbox=rect, all_screens=True).convert("RGB")
                if name == "pillow"
                else win32.capture_region(rect)
            )
            elapsed = time.perf_counter() - started
            if i:
                samples[name].append(elapsed)
        if ImageChops.difference(images["pillow"], images["region_gdi"]).getbbox():
            mismatch += 1
    if mismatch:
        raise RuntimeError(f"Capture pixel mismatch in {mismatch} comparisons")
    print(
        json.dumps(
            {
                "same_pixels": True,
                "timings": {
                    name: {"median_ms": round(statistics.median(values) * 1000, 3), "samples": len(values)}
                    for name, values in samples.items()
                },
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
