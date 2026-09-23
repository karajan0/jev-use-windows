"""Compare complete target results with a saved pre-change visual.py implementation."""

import argparse
import importlib.util
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageDraw

from jev_use import visual


def icon(drop=False):
    image = Image.new("RGB", (48, 48), (20, 45, 85))
    draw = ImageDraw.Draw(image)
    draw.rectangle((2, 2, 45, 45), outline="white", width=2)
    if drop:
        draw.ellipse((10, 10, 38, 38), outline="orange", width=5)
    else:
        draw.polygon([(13, 10), (36, 24), (13, 38)], fill=(40, 230, 170))
    return image


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("jev_use._visual_baseline", args.baseline)
    baseline = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = baseline
    spec.loader.exec_module(baseline)
    report = {}
    with tempfile.TemporaryDirectory(prefix="jev-visual-bench-") as directory:
        refs = {}
        for label, asset in (("launch", icon()), ("drop", icon(True))):
            path = Path(directory) / f"{label}.png"
            asset.save(path)
            refs[label] = str(path)
        for scenario in ("unchanged", "dynamic"):
            timings = {"baseline": [], "updated": []}
            visual._frame_cache.last = (None, {})
            for index in range(24):
                image = Image.new("RGB", (1080, 720), (15, 15, 25))
                x = 40 if scenario == "unchanged" else 40 + index * 10
                image.paste(icon(), (x, 60))
                image.paste(icon(True), (800, 400))
                duplicate = scenario == "dynamic" and index % 6 == 0
                if duplicate:
                    image.paste(icon(), (800, 60))
                outcomes = {}
                # Alternate order to reduce systematic warmup/order bias.
                engines = [("baseline", baseline), ("updated", visual)]
                for name, module in engines[:: -1 if index % 2 else 1]:
                    started = time.perf_counter()
                    outcomes[name] = module.locate_targets(image, (0, 0, 1080, 720), refs)
                    timings[name].append((time.perf_counter() - started) * 1000)
                assert outcomes["baseline"] == outcomes["updated"], (scenario, index)
                assert len(outcomes["updated"]) == (1 if duplicate else 2)
            report[scenario] = {
                "equivalent_cases": 24,
                **{
                    name: {
                        "median_ms": round(statistics.median(values), 3),
                        "p95_ms": round(sorted(values)[22], 3),
                    }
                    for name, values in timings.items()
                },
            }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
