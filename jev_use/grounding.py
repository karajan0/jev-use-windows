"""Goal-aware candidate budgets and local visual control proposals."""

import heapq
import math
import re
from collections import Counter
from functools import lru_cache

import cv2
import numpy as np


@lru_cache(maxsize=1)
def _glyph_templates():
    templates = []
    for thickness in (2, 4, 6):
        for name, strokes in (
            ("X-shaped close icon", [((3, 3), (20, 20)), ((20, 3), (3, 20))]),
            ("right chevron expand icon", [((7, 3), (17, 12)), ((17, 12), (7, 20))]),
            ("down chevron dropdown icon", [((3, 7), (12, 17)), ((12, 17), (20, 7))]),
            ("plus icon", [((12, 3), (12, 20)), ((3, 12), (20, 12))]),
        ):
            mask = np.zeros((24, 24), dtype=np.uint8)
            for start, end in strokes:
                cv2.line(mask, start, end, 1, thickness)
            ys, xs = np.nonzero(mask)
            mask = cv2.resize(
                mask[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1], (24, 24), interpolation=cv2.INTER_NEAREST
            )
            templates.append((name, mask.astype(bool)))
    for name, points in (
        ("right triangle play or expand icon", [(2, 2), (21, 12), (2, 21)]),
        ("down triangle dropdown icon", [(2, 2), (21, 2), (12, 21)]),
    ):
        mask = np.zeros((24, 24), dtype=np.uint8)
        cv2.fillPoly(mask, [np.array(points, dtype=np.int32)], 1)
        ys, xs = np.nonzero(mask)
        mask = cv2.resize(
            mask[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1], (24, 24), interpolation=cv2.INTER_NEAREST
        )
        templates.append((name, mask.astype(bool)))
    return templates


def describe_glyph(gray):
    if min(gray.shape) < 4:
        return "Unlabeled visual control"
    border = np.concatenate((gray[0], gray[-1], gray[:, 0], gray[:, -1]))
    foreground = (np.abs(gray.astype(np.float32) - np.median(border)) > 40).astype(np.uint8)
    ys, xs = np.nonzero(foreground)
    if len(xs) < 6:
        return "Unlabeled visual control"
    foreground = foreground[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
    if not 0.5 <= foreground.shape[1] / foreground.shape[0] <= 2:
        return "Unlabeled visual control"
    normalized = cv2.resize(foreground, (24, 24), interpolation=cv2.INTER_NEAREST).astype(bool)
    scores = [
        (float(np.logical_and(normalized, mask).sum()) / max(1, np.logical_or(normalized, mask).sum()), name)
        for name, mask in _glyph_templates()
    ]
    score, name = max(scores)
    return name if score >= 0.62 else "Unlabeled visual control"


def spatial_context(rect, words):
    cx, cy = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
    centers = [
        ((w.rect[0] + w.rect[2]) / 2, (w.rect[1] + w.rect[3]) / 2, w.text)
        for w in words
        if w.rect != rect and len(w.text) > 1
    ]
    nearby = heapq.nsmallest(3, centers, key=lambda item: (item[0] - cx) ** 2 + (item[1] - cy) ** 2)
    context = []
    for tx, ty, label in nearby:
        dx, dy = cx - tx, cy - ty
        distance = math.hypot(dx, dy)
        if distance > 240:
            continue
        direction = ("right of" if dx > 0 else "left of") if abs(dx) > abs(dy) else ("below" if dy > 0 else "above")
        context.append(f"{direction} '{label}' ({round(distance)}px)")
    return "; ".join(context)


def tokens(value):
    return set(re.findall(r"[^\W_]+", value.casefold(), flags=re.UNICODE))


def select_actions(goal, actions, limit=140):
    """Keep relevant late-screen controls; reserve coverage for low lexical overlap."""
    candidates = [(key, action) for key, action in actions.items() if action.target is not None]
    if len(candidates) <= limit:
        return actions
    stop = tokens(
        "the a an to of in on at and or with for this that click ui element described by instruction single step grounding only"
    )
    query = tokens(goal) - stop
    documents = {key: tokens(action.target.label + " " + action.target.context) for key, action in candidates}
    frequency = Counter(token for document in documents.values() for token in document)
    weights = {token: math.log(1 + len(candidates) / (1 + frequency[token])) for token in query}

    def score(item):
        key, action = item
        label = tokens(action.target.label)
        return sum(weights[token] * (4 if token in label else 1) for token in query & documents[key]) + (
            1 if action.target.focused else 0
        )

    ranked = sorted(candidates, key=score, reverse=True)
    chosen = {key for key, _ in ranked[: limit - 28]}
    remaining = [(key, action) for key, action in candidates if key not in chosen]
    # Uniform spatial coverage avoids spending the entire remaining budget on
    # the first rows. The selected keys still refer to the original action map.
    remaining.sort(key=lambda item: (item[1].target.center[1], item[1].target.center[0]))
    for index in range(min(28, len(remaining))):
        chosen.add(remaining[index * len(remaining) // min(28, len(remaining))][0])
    return {key: action for key, action in actions.items() if action.target is None or key in chosen}


def visual_controls(image, words, bounds):
    """Propose small edge components near text; descriptions are geometric only.

    No learned icon semantics or guessed coordinates. Live input still requires
    pixel identity validation. OCR rectangles are excluded from proposals.
    """
    from .desktop import Target

    gray = cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, 60, 160)
    for word in words:
        x1, y1, x2, y2 = word.rect
        edges[max(0, y1 - 2) : min(image.height, y2 + 2), max(0, x1 - 2) : min(image.width, x2 + 2)] = 0
    connected = cv2.dilate(edges, np.ones((3, 3), dtype=np.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats(connected, connectivity=8)
    targets = []
    centers = [((w.rect[0] + w.rect[2]) / 2, (w.rect[1] + w.rect[3]) / 2, w.text) for w in words]
    for index in range(1, count):
        x, y, width, height, area = (int(v) for v in stats[index])
        if not (6 <= width <= 64 and 5 <= height <= 48 and area >= 12 and 0.2 <= width / height <= 5):
            continue
        cx, cy = x + width / 2, y + height / 2
        nearby = heapq.nsmallest(3, centers, key=lambda item: (item[0] - cx) ** 2 + (item[1] - cy) ** 2)
        if not nearby or math.hypot(nearby[0][0] - cx, nearby[0][1] - cy) > 180:
            continue
        # Discard components whose center is text, including boundary fragments.
        if any(w.rect[0] - 2 <= cx <= w.rect[2] + 2 and w.rect[1] - 2 <= cy <= w.rect[3] + 2 for w in words):
            continue
        context = []
        for tx, ty, label in nearby:
            dx, dy = cx - tx, cy - ty
            direction = ("right of" if dx > 0 else "left of") if abs(dx) > abs(dy) else ("below" if dy > 0 else "above")
            context.append(f"{direction} '{label}' ({round(math.hypot(dx, dy))}px)")
        rect = (x + bounds[0], y + bounds[1], x + width + bounds[0], y + height + bounds[1])
        description = describe_glyph(gray[y : y + height, x : x + width])
        targets.append(Target("click", description, rect, "Visual", context="; ".join(context)))
    return targets
