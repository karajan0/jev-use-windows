"""Ground-truth-blind tiled OCR ablation, scored only after all words are found.

Measures candidate reachability, NOT Jev task success or selected-point accuracy.
No changes to the installed OCR pipeline and no API requests.
"""

import argparse
import json
import statistics
import time
from pathlib import Path

from PIL import Image

from jev_use import ocr


def contains(point, box):
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


def tiled_words(image):
    words = []
    # Each tile remains below the production reader's 1800px downscale limit.
    # 64px context on each edge protects words crossing tile boundaries.
    for y in range(0, image.height, 1000):
        for x in range(0, image.width, 1000):
            left, top = max(0, x - 64), max(0, y - 64)
            crop = image.crop((left, top, min(image.width, x + 1064), min(image.height, y + 1064)))
            for word in ocr.read(crop, "en-US"):
                point = (left + (word.rect[0] + word.rect[2]) // 2, top + (word.rect[1] + word.rect[3]) // 2)
                if x <= point[0] < min(image.width, x + 1000) and y <= point[1] < min(image.height, y + 1000):
                    words.append({"text": word.text, "point": point})
    return words


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads((args.input / "manifest.json").read_text(encoding="utf-8"))
    results = []
    for index, sample in enumerate(manifest["samples"]):
        with Image.open(args.input / "images" / sample["img_filename"]) as original:
            image = original.convert("RGB")
        started = time.perf_counter()
        words = tiled_words(image)
        elapsed = (time.perf_counter() - started) * 1000
        matches = [w for w in words if contains(w["point"], sample["bbox"])]
        results.append(
            {
                "id": sample["id"],
                "ui_type": sample["ui_type"],
                "reachable": bool(matches),
                "matches": matches,
                "word_count": len(words),
                "milliseconds": round(elapsed, 3),
            }
        )
        print(f"{index + 1}/{len(manifest['samples'])} reachable={bool(matches)}", flush=True)
    ocr.close_worker()
    summary = {
        "samples": len(results),
        "reachable": sum(r["reachable"] for r in results),
        "median_ms": round(statistics.median(r["milliseconds"] for r in results), 3),
        "note": "Candidate ceiling only; no model decisions, no 70-target cap, no task success claim",
        "results": results,
    }
    (args.input / "tiled-ocr-diagnostic.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "results"}, indent=2))


if __name__ == "__main__":
    main()
