"""Local image matching and spatial change analysis for non-native interfaces."""

import hashlib
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


@dataclass(frozen=True)
class Match:
    rect: tuple[int, int, int, int]
    score: float


def overlap(a, b):
    width = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    height = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    intersection = width * height
    return intersection / max(1, (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection)


def changed_fraction(before, after):
    if before.size != after.size:
        return 1.0
    a = np.asarray(before.convert("RGB"), dtype=np.int16)
    b = np.asarray(after.convert("RGB"), dtype=np.int16)
    return float(np.mean(np.max(np.abs(a - b), axis=2) > 24))


def changed_regions(before, after, tile=64):
    """Return meaningful tile changes; tiny pixel noise does not count."""
    if before.size != after.size:
        return [(0, 0, after.width, after.height)]
    a = np.asarray(before.convert("RGB"), dtype=np.int16)
    b = np.asarray(after.convert("RGB"), dtype=np.int16)
    difference = np.max(np.abs(a - b), axis=2) > 24
    regions = []
    for y in range(0, after.height, tile):
        for x in range(0, after.width, tile):
            part = difference[y : y + tile, x : x + tile]
            if part.mean() >= 0.02:
                regions.append((x, y, min(x + tile, after.width), min(y + tile, after.height)))
    return regions


@lru_cache(maxsize=32)
def _prepared_template(size, pixels, scales):
    needle = np.frombuffer(pixels, dtype=np.uint8).reshape(size[1], size[0], 3)
    if min(size) < 8 or float(needle.std(axis=(0, 1)).max()) < 12:
        return ()
    gray_ok = cv2.cvtColor(needle, cv2.COLOR_RGB2GRAY).std() >= 6
    prepared = []
    for scale in scales:
        width, height = round(size[0] * scale), round(size[1] * scale)
        if width < 8 or height < 8:
            continue
        resized = cv2.resize(needle, (width, height), interpolation=cv2.INTER_LINEAR)
        prepared.append((resized, cv2.cvtColor(resized, cv2.COLOR_RGB2GRAY) if gray_ok else resized, gray_ok))
    return tuple(prepared)


def _match_prepared(source, source_gray, prepared, threshold):
    candidates = []
    for resized, search, gray_ok in prepared:
        height, width = resized.shape[:2]
        if width > source.shape[1] or height > source.shape[0]:
            continue
        scores = cv2.matchTemplate(
            source_gray if gray_ok else source,
            search,
            cv2.TM_CCOEFF_NORMED,
        )
        for _ in range(3):
            _, score, _, (x, y) = cv2.minMaxLoc(scores)
            if not np.isfinite(score) or score < threshold - 0.08:
                break
            # Grayscale search is faster; verify color before accepting the location.
            color_error = cv2.absdiff(source[y : y + height, x : x + width], resized).mean()
            if color_error <= 20:
                candidates.append(Match((x, y, x + width, y + height), float(score)))
            # Suppress neighboring offsets of this same object before finding another.
            scores[max(0, y - height // 2) : y + height // 2 + 1, max(0, x - width // 2) : x + width // 2 + 1] = -1
    candidates.sort(key=lambda item: item.score, reverse=True)
    if not candidates or candidates[0].score < threshold:
        return None
    best = candidates[0]
    other = next((item for item in candidates[1:] if overlap(item.rect, best.rect) < 0.4), None)
    if other and best.score - other.score < 0.08:
        return None
    return best


def unique_match(image, template, *, scales=(0.75, 1.0, 1.25, 1.5), threshold=0.92):
    """Search the entire frame so off-target duplicates are still rejected."""
    source = np.asarray(image.convert("RGB"))
    prepared = _prepared_template(template.size, template.convert("RGB").tobytes(), tuple(scales))
    return _match_prepared(source, cv2.cvtColor(source, cv2.COLOR_RGB2GRAY), prepared, threshold)


@lru_cache(maxsize=32)
def _load_template(path, modified):
    with Image.open(path) as image:
        if not (8 <= image.width <= 512 and 8 <= image.height <= 512):
            raise ValueError("Visual target images must be between 8 and 512 pixels on each side")
        return image.convert("RGB")


def load_template(path):
    path = Path(path).resolve(strict=True)
    return _load_template(str(path), path.stat().st_mtime_ns)


_frame_cache = threading.local()


def locate_targets(image, bounds, references):
    from .desktop import Target

    if not references:
        return []
    source = np.asarray(image.convert("RGB"))
    frame_key = (image.size, hashlib.blake2s(source).digest())
    previous_key, previous = getattr(_frame_cache, "last", (None, {}))
    if previous_key != frame_key:
        previous = {}
    current = {}
    source_gray = None
    targets = []
    for label, path in references.items():
        template = load_template(path)
        pixels = template.tobytes()
        key = (template.size, hashlib.blake2s(pixels).digest())
        if key in previous:
            match = previous[key]
        else:
            if source_gray is None:
                source_gray = cv2.cvtColor(source, cv2.COLOR_RGB2GRAY)
            prepared = _prepared_template(template.size, pixels, (0.75, 1.0, 1.25, 1.5))
            match = _match_prepared(source, source_gray, prepared, 0.92)
        current[key] = match
        if match:
            x1, y1, x2, y2 = match.rect
            targets.append(
                Target("click", label, (x1 + bounds[0], y1 + bounds[1], x2 + bounds[0], y2 + bounds[1]), "Visual")
            )
    # Retain results only, never screenshots; any pixel change invalidates matches.
    _frame_cache.last = (frame_key, dict(list(current.items())[:16]))
    return targets


def reacquire(target, before, after):
    """Match identity first. Never silently substitute a same-name native control."""
    if before.window.handle != after.window.handle:
        return None
    if target.runtime_id:
        matches = [
            item
            for item in after.targets
            if item.runtime_id == target.runtime_id
            and item.role == target.role
            and item.label == target.label
            and item.kind == target.kind
        ]
        return matches[0] if len(matches) == 1 else None
    if target.role not in {"OCR", "Visual"}:
        return None
    matches = [item for item in after.targets if item.role == target.role and item.label == target.label]
    if len(matches) != 1:
        return None
    bounds = before.bounds or before.window.rect
    rect = tuple(value - bounds[index % 2] for index, value in enumerate(target.rect))
    if rect[0] < 0 or rect[1] < 0 or rect[2] > before.image.width or rect[3] > before.image.height:
        return None
    match = unique_match(after.image, before.image.crop(rect))
    if match is None:
        return None
    new_bounds = after.bounds or after.window.rect
    detected = tuple(value + new_bounds[index % 2] for index, value in enumerate(match.rect))
    return matches[0] if overlap(detected, matches[0].rect) >= 0.6 else None


def semantic_state(screen):
    # Pixels are deliberately excluded: background animation is not task progress.
    controls = sorted(
        (item.kind, item.role, item.label, item.value, item.focused, item.rect) for item in screen.targets
    )
    return screen.window.handle, tuple(screen.text), tuple(screen.postcondition_text), tuple(controls)


def action_state(screen, target):
    """Conservative repeat guard: passive remote text and geometry are not progress.

    Completion evidence is checked separately. This intentionally stops a repeated
    input when only an unrelated clock, subtitle, or target position has changed.
    """
    if target is None:
        return semantic_state(screen)
    region = (target.rect[0] - 24, target.rect[1] - 24, target.rect[2] + 24, target.rect[3] + 24)
    controls = sorted(
        (item.kind, item.role, item.runtime_id, item.label, item.value, item.focused)
        for item in screen.targets
        if item.role != "OCR" or overlap(item.rect, region) > 0
    )
    return screen.window.handle, tuple(controls)


def transition(before, after, target=None):
    if before.window.handle != after.window.handle or before.window.rect != after.window.rect:
        return {"effect": "window_changed", "changed_regions": []}
    regions = changed_regions(before.image, after.image)
    if semantic_state(before) != semantic_state(after):
        effect = "state_changed"
    elif target is not None:
        bounds = before.bounds or before.window.rect
        rect = (
            max(0, target.rect[0] - bounds[0] - 16),
            max(0, target.rect[1] - bounds[1] - 16),
            min(before.image.width, target.rect[2] - bounds[0] + 16),
            min(before.image.height, target.rect[3] - bounds[1] + 16),
        )
        relevant = any(overlap(region, rect) > 0 for region in regions)
        effect = "target_region_changed" if relevant else "background_change_only" if regions else "suspected_noop"
    else:
        effect = "unverified_visual_change" if regions else "suspected_noop"
    merged = []
    for region in regions:
        for index, box in enumerate(merged):
            if region[0] <= box[2] and region[2] >= box[0] and region[1] <= box[3] and region[3] >= box[1]:
                merged[index] = (
                    min(box[0], region[0]),
                    min(box[1], region[1]),
                    max(box[2], region[2]),
                    max(box[3], region[3]),
                )
                break
        else:
            merged.append(region)
    return {"effect": effect, "changed_regions": merged[:8], "region_count": len(regions)}
