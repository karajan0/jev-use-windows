"""Paired real-screenshot OCR replay, with exact word/box/line equality checks."""

import argparse
import importlib.util
import json
import statistics
import sys
import time
from pathlib import Path

from PIL import Image

from jev_use import desktop, ocr


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    old_ocr = module("jev_use._speed_old_ocr", args.baseline / "jev_use" / "ocr.py")
    old_desktop = module("jev_use._speed_old_desktop", args.baseline / "jev_use" / "desktop.py")
    old_desktop.ocr = old_ocr
    manifest = json.loads((args.data / "manifest.json").read_text(encoding="utf-8"))
    results = []
    for sample in manifest["samples"][::4]:
        with Image.open(args.data / "images" / sample["img_filename"]) as original:
            image = original.convert("RGB")
        bounds = (0, 0, image.width, image.height)
        caches = {"baseline": old_desktop._OcrFrameCache(), "updated": desktop._OcrFrameCache()}
        old_ocr.close_worker()
        ocr.close_worker()
        frames = [image]
        for value in (18, 34, 50):
            changed = image.copy()
            changed.paste((value, value, value), (0, int(image.height * 0.4), image.width, image.height))
            frames.append(changed)
        for index, frame in enumerate(frames):
            outcomes, times = {}, {}
            names = ("baseline", "updated") if index % 2 == 0 else ("updated", "baseline")
            for name in names:
                started = time.perf_counter()
                words = caches[name].read(frame, 1, bounds, "en-US")
                times[name] = (time.perf_counter() - started) * 1000
                outcomes[name] = [(w.text, w.rect, w.line) for w in words]
            assert outcomes["baseline"] == outcomes["updated"], (sample["id"], index)
            results.append({"id": sample["id"], "frame": index, "equal": True, **times})
            print(f"{sample['id']} frame {index}: {times}", flush=True)
    old_ocr.close_worker()
    ocr.close_worker()
    summary = {"cases": len(results), "exact_matches": len(results)}
    for phase, predicate in (("cold", lambda i: i == 0), ("changing", lambda i: i > 0)):
        selected = [row for row in results if predicate(row["frame"])]
        summary[phase] = {
            name + "_median_ms": round(statistics.median(r[name] for r in selected), 3)
            for name in ("baseline", "updated")
        }
    args.output.write_text(json.dumps({"summary": summary, "results": results}, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
