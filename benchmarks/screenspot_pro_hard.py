"""Frozen hard ScreenSpot-Pro subset; real Jev OCR/decision, no desktop input.

Ground truth is used only for predeclared sampling and scoring after prediction.
No target crops, reference images or accessibility metadata are given to Jev.
This is screenshot grounding, not an end-to-end desktop task benchmark.
"""

import argparse
import hashlib
import json
import statistics
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from typesafe_sdk import TypeSafeClient

from jev_use import credentials, desktop, ocr, runner, win32

try:
    from jev_use.grounding import select_actions
except ModuleNotFoundError as exc:
    if exc.name != "jev_use.grounding":
        raise

    def select_actions(goal, actions):
        """Original runtime offers every action created by its 70-word observer."""
        return actions


REVISION = "210e78d3844251110bff86c95835ebd37a6930fa"
BASE = f"https://huggingface.co/datasets/likaixin/ScreenSpot-Pro/resolve/{REVISION}/"
APPS = ("autocad", "blender", "inventor", "photoshop", "premiere", "solidworks", "unreal_engine", "vivado")


def download(relative, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        return
    url = BASE + urllib.parse.quote(relative, safe="/")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=45) as response:
                data = response.read()
            destination.write_bytes(data)
            return
        except OSError:
            if attempt == 2:
                raise


def fraction(item):
    x1, y1, x2, y2 = item["bbox"]
    return (x2 - x1) * (y2 - y1) / (item["img_size"][0] * item["img_size"][1])


def prepare(root, suite="hard"):
    selected = []
    for app in APPS:
        path = root / "annotations" / f"{app}_windows.json"
        download(f"annotations/{app}_windows.json", path)
        rows = json.loads(path.read_text(encoding="utf-8"))
        excluded_images = set()
        if suite == "heldout":
            for kind in ("icon", "text"):
                ordered = sorted(
                    (item for item in rows if item["ui_type"] == kind), key=lambda item: (fraction(item), item["id"])
                )
                excluded_images.update(item["img_filename"] for item in ordered[:2])
                if kind == "text":
                    excluded_images.add(ordered[-1]["img_filename"])
        for kind in ("text",) if suite == "control" else ("icon", "text"):
            candidates = sorted(
                (item for item in rows if item["ui_type"] == kind), key=lambda item: (fraction(item), item["id"])
            )
            if suite == "heldout":
                candidates = sorted(
                    (item for item in candidates if item["img_filename"] not in excluded_images),
                    key=lambda item: hashlib.sha256(item["id"].encode()).hexdigest(),
                )
            selected.extend(candidates[-1:] if suite == "control" else candidates[:2])
    manifest = {
        "dataset": "likaixin/ScreenSpot-Pro",
        "revision": REVISION,
        "selection": (
            "Two targets per UI type per app ordered by SHA256(ID), excluding every hard/control screenshot"
            if suite == "heldout"
            else "Largest relative-area text target per predeclared Windows app; ties by ID"
            if suite == "control"
            else "Two smallest relative-area targets per UI type per predeclared Windows app; ties by ID"
        ),
        "apps": APPS,
        "samples": selected,
    }

    def fetch(item):
        download("images/" + item["img_filename"], root / "images" / item["img_filename"])

    with ThreadPoolExecutor(max_workers=4) as pool:
        for index, _ in enumerate(pool.map(fetch, selected), 1):
            if index % 4 == 0:
                print(f"Downloaded {index}/{len(selected)}", flush=True)
    return manifest


def inside(point, box):
    return point is not None and box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


def predict(client, image, instruction, index):
    """Only pixels and instruction cross this prediction boundary."""
    bounds = (0, 0, image.width, image.height)
    window = win32.Window(index + 1, "ScreenSpot-Pro screenshot", bounds)
    started = time.perf_counter()
    # Static datasets contain no UIA tree. Use the production OCR construction.
    with patch.object(desktop, "_uia_targets", return_value=([], [])):
        screen = desktop._finish_observation(window, image, image, bounds, bounds, "en-US")
    observation_ms = (time.perf_counter() - started) * 1000
    actions = runner._actions(screen, None, None, {}, None)
    goal = "Click the UI element described by this instruction (single-step grounding only): " + instruction
    offered = select_actions(goal, actions)
    started = time.perf_counter()
    choice, confidence, proof = runner._choose(
        client,
        goal,
        screen,
        actions,
        [],
    )
    decision_ms = (time.perf_counter() - started) * 1000
    action = actions.get(choice)
    point = action.target.center if action and action.kind == "click" and action.target else None
    accepted = point if confidence >= 0.35 else None
    return {
        "choice": choice,
        "confidence": confidence,
        "predicted_point": point,
        "accepted_point": accepted,
        "chosen_label": action.target.label if action and action.target else None,
        "proof": proof,
        "observation_ms": round(observation_ms, 3),
        "decision_ms": round(decision_ms, 3),
        "candidate_points": [action.target.center for action in offered.values() if action.target],
        "all_ocr_points": [
            ((word.rect[0] + word.rect[2]) // 2, (word.rect[1] + word.rect[3]) // 2)
            for word in desktop._ocr_cache.words
        ],
        "candidates": [{"label": target.label, "rect": target.rect} for target in screen.targets],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--suite", choices=("hard", "control", "heldout"), default="hard")
    parser.add_argument("--data-root", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    data_root = args.data_root or args.output
    manifest = prepare(data_root, args.suite)
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if args.prepare_only:
        return
    provider = credentials.load(Path(__file__).resolve().parents[1])
    options = {"api_key": provider.key, "timeout": 25.0}
    if provider.base_url:
        options["base_url"] = provider.base_url
    if provider.model:
        options["model"] = provider.model
    results = []
    with TypeSafeClient(**options) as client:
        for index, sample in enumerate(manifest["samples"]):
            path = data_root / "images" / sample["img_filename"]
            result = {
                "id": sample["id"],
                "application": sample["application"],
                "ui_type": sample["ui_type"],
                "instruction": sample["instruction"],
                "bbox": sample["bbox"],
                "img_size": sample["img_size"],
                "image_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "correct": False,
            }
            try:
                with Image.open(path) as original:
                    image = original.convert("RGB")
                assert list(image.size) == sample["img_size"]
                result.update(predict(client, image, sample["instruction"], index))
                result["correct"] = inside(result["accepted_point"], sample["bbox"])
                result["raw_correct"] = inside(result["predicted_point"], sample["bbox"])
                result["candidate_reachable"] = any(inside(p, sample["bbox"]) for p in result.pop("candidate_points"))
                result["ocr_reachable"] = any(inside(p, sample["bbox"]) for p in result.pop("all_ocr_points"))
                result["failure_stage"] = (
                    None
                    if result["correct"]
                    else "candidate_cap"
                    if result["ocr_reachable"] and not result["candidate_reachable"]
                    else "perception"
                    if not result["candidate_reachable"]
                    else "decision_or_abstention"
                )
            except Exception as exc:
                # Do not log HTTP bodies, request headers, or credentials.
                result["error_type"] = type(exc).__name__
            results.append(result)
            (args.output / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
            print(
                f"{index + 1}/{len(manifest['samples'])} {sample['id']}: correct={result['correct']} "
                f"stage={result.get('failure_stage', result.get('error_type'))}",
                flush=True,
            )
    desktop._ocr_pool.submit(ocr.close_worker).result()
    summary = {
        "protocol": f"ScreenSpot-Pro {args.suite} subset, screenshot-only Jev adapter; no live clicks",
        "runtime_files": {module.__name__: module.__file__ for module in (ocr, desktop, runner)},
        "samples": len(results),
        "correct": sum(r["correct"] for r in results),
        "errors": sum("error_type" in r for r in results),
        "candidate_reachable": sum(r.get("candidate_reachable", False) for r in results),
        "ocr_reachable": sum(r.get("ocr_reachable", False) for r in results),
        "raw_correct": sum(r.get("raw_correct", False) for r in results),
    }
    for kind in ("icon", "text"):
        group = [r for r in results if r["ui_type"] == kind]
        summary[kind] = {"correct": sum(r["correct"] for r in group), "total": len(group)}
    for metric in ("observation_ms", "decision_ms"):
        values = [r[metric] for r in results if metric in r]
        summary[metric + "_median"] = round(statistics.median(values), 3) if values else None
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
