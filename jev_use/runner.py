"""One Jev decision per desktop step, with a proof choice in the same call."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

from typesafe_sdk import Choice, TypeSafeClient

from . import win32
from .credentials import Provider
from .desktop import Observation, Target, observe_controls, observe_desktop


@dataclass(frozen=True)
class Action:
    kind: str
    label: str
    target: Target | None = None
    value: str | None = None


def _actions(
    screen: Observation, text: str | None, field: str | None, fills: dict[str, str], url: str | None
) -> dict[str, Action]:
    actions = {
        "done": Action("done", "The CURRENT screen visibly proves the entire goal is complete."),
        "blocked": Action("blocked", "No safe visible action can advance the goal."),
    }
    for index, target in enumerate(screen.targets):
        if target.kind == "text":
            if target.value:
                continue
            value = fills.get(target.label.casefold())
            if value is None and text is not None and (not field or field.casefold() in target.label.casefold()):
                value = text
            if value is None:
                continue
            actions[f"t{index}"] = Action(
                "type", f"Type the supplied exact text in {target.role} '{target.label}'", target, value
            )
        else:
            actions[f"c{index}"] = Action("click", f"Click {target.role} '{target.label}'", target)
    for name in ("Enter", "Tab", "Escape"):
        actions[f"key_{name.lower()}"] = Action("key", f"Press {name}", value=name)
    actions["scroll_down"] = Action("scroll", "Scroll down in the selected window", value="down")
    actions["scroll_up"] = Action("scroll", "Scroll up in the selected window", value="up")
    if url:
        actions["open_url"] = Action("url", f"Open the supplied HTTPS URL: {url}", value=url)
    if text is None and not fills and not url:
        actions["needs_input"] = Action("needs_input", "An exact text or URL value is needed but was not supplied.")
    return actions


def _evidence(screen: Observation) -> dict[str, str]:
    candidates = list(dict.fromkeys([*screen.text[:60], *screen.ocr_lines[:20]]))[:80]
    return {"none": "No visible text proves the goal."} | {
        f"p{index}": item[:180] for index, item in enumerate(candidates)
    }


def _visible_text(screen: Observation) -> list[str]:
    # A dense UIA tree can put a visual-only status past the state limit.
    # OCR lines are ordered with text outside UIA controls first.
    return list(dict.fromkeys([*screen.ocr_lines[:24], *screen.text]))[:120]


def _snapshot_id(screen: Observation) -> str:
    bounds = screen.bounds or screen.window.rect
    rect = screen.window.rect
    crop = (
        max(bounds[0], rect[0]) - bounds[0],
        max(bounds[1], rect[1]) - bounds[1],
        min(bounds[2], rect[2]) - bounds[0],
        min(bounds[3], rect[3]) - bounds[1],
    )
    pixels = screen.image.crop(crop).convert("L").resize((64, 64)).tobytes()
    return hashlib.blake2s(screen.window.handle.to_bytes(8, "little") + pixels, digest_size=8).hexdigest()


def _choose(
    client: TypeSafeClient, goal: str, screen: Observation, actions: dict[str, Action], history: list[str]
) -> tuple[str, float, str]:
    response = client.system_one(
        state={
            "goal": goal,
            "window": screen.window.title,
            "visible_text": _visible_text(screen),
            "recent_actions": history[-8:],
        },
        questions={
            "next": Choice(
                instructions=(
                    "Choose exactly one next action for the user's goal. Use 'done' only when this CURRENT screen "
                    "proves completion. A draft in an editable field is not proof that a message was sent. "
                    "Use 'needs_input' when an exact value is missing. Avoid repeating an action that did not change the screen."
                ),
                criteria={key: action.label for key, action in actions.items()},
            ),
            "proof": Choice(
                instructions="Choose visible text that supports completion, or 'none' when the goal is not yet visibly complete.",
                criteria=_evidence(screen),
            ),
        },
    )
    answer = response.answers["next"]
    proof = response.answers["proof"].choice
    return answer.choice, answer.confidence, proof


def _perform(action: Action, screen: Observation) -> None:
    if win32.select_window(None).handle != screen.window.handle:
        raise RuntimeError("The foreground window changed after observation; no input was sent")
    if win32.rect_of(screen.window.handle) != screen.window.rect:
        raise RuntimeError("The foreground window moved after observation; no input was sent")
    if action.kind in {"click", "type"}:
        if action.target is None:
            raise RuntimeError("No target for the selected action")
        x, y = action.target.center
        left, top, right, bottom = screen.window.rect
        if not (left <= x < right and top <= y < bottom):
            raise RuntimeError("The selected target is outside the window")
        win32.click(x, y)
        if action.kind == "type":
            win32.type_text(action.value or "")
    elif action.kind == "key":
        win32.press({"Enter": 0x0D, "Tab": 0x09, "Escape": 0x1B}[action.value or ""])
    elif action.kind == "scroll":
        win32.scroll(1 if action.value == "up" else -1)
    elif action.kind == "url":
        win32.hotkey(0x11, 0x4C)  # Ctrl+L
        win32.type_text(action.value or "")
        win32.press(0x0D)
    else:
        raise RuntimeError("Invalid action")


def run(
    goal: str,
    window: win32.Window,
    provider: Provider,
    *,
    text: str | None = None,
    field: str | None = None,
    fills: dict[str, str] | None = None,
    url: str | None = None,
    language: str | None = None,
    max_steps: int = 12,
    min_confidence: float = 0.35,
    delay: float = 0.08,
) -> dict:
    started = time.perf_counter()
    history: list[str] = []
    calls = 0
    prior: tuple[str, str] | None = None
    evidence = None
    current_window = window
    supplied_fills = dict(fills or {})
    client_options = {"api_key": provider.key, "timeout": 25.0}
    if provider.base_url:
        client_options["base_url"] = provider.base_url
    if provider.model:
        client_options["model"] = provider.model

    def failed(exc: Exception, attempted: str | None = None) -> dict:
        return {
            "status": "uncertain" if history or attempted else "failed",
            "error": str(exc),
            "goal": goal,
            "window": current_window.title,
            "actions": history,
            "attempted": attempted,
            "jevCalls": calls,
            "seconds": round(time.perf_counter() - started, 3),
        }

    with TypeSafeClient(**client_options) as client:
        try:
            win32.activate(window.handle)
        except Exception as exc:
            return failed(exc)
        for _step in range(max_steps):
            try:
                screen = observe_desktop(language=language)
                if prior is not None and _snapshot_id(screen) == prior[1]:
                    time.sleep(0.18)
                    screen = observe_desktop(language=language)
                current_window = screen.window
            except Exception as exc:
                return failed(exc)
            actions = _actions(screen, text, field, supplied_fills, url)
            try:
                choice, confidence, proof = _choose(client, goal, screen, actions, history)
                calls += 1
                if confidence < min_confidence:
                    time.sleep(0.18)
                    refreshed = observe_desktop(language=language)
                    if _snapshot_id(refreshed) != _snapshot_id(screen):
                        screen = refreshed
                        current_window = screen.window
                        actions = _actions(screen, text, field, supplied_fills, url)
                        choice, confidence, proof = _choose(client, goal, screen, actions, history)
                        calls += 1
            except Exception as exc:
                return failed(exc)
            if choice not in actions or confidence < min_confidence:
                status = "uncertain"
                break
            action = actions[choice]
            if action.kind == "done":
                evidence = _evidence(screen).get(proof) if proof != "none" else None
                status = "completed" if proof != "none" and evidence else "not_achieved"
                break
            if action.kind in {"needs_input", "blocked"}:
                status = action.kind
                break
            signature = _snapshot_id(screen)
            if prior == (choice, signature):
                status = "stalled"
                break
            prior = (choice, signature)
            try:
                _perform(action, screen)
            except Exception as exc:
                return failed(exc, action.label)
            history.append(action.label)
            if action.kind == "type":
                if text == action.value:
                    text = None
                supplied_fills.pop(action.target.label.casefold(), None)
            if action.kind == "url":
                url = None
            time.sleep(delay)
        else:
            status = "step_limit"
    return {
        "status": status,
        "goal": goal,
        "window": current_window.title,
        "actions": history,
        "evidence": evidence,
        "jevCalls": calls,
        "seconds": round(time.perf_counter() - started, 3),
    }


def batch(
    goal: str,
    window: win32.Window,
    provider: Provider,
    labels: list[str],
    *,
    delay: float = 0.02,
    dry_run: bool = False,
) -> dict:
    """Map exact labels once, then execute a stable visible panel with one Jev verification call."""
    started = time.perf_counter()
    current, controls = observe_controls(window)
    mapped: list[Target] = []
    for label in labels:
        matches = [item for item in controls if item.label.casefold() == label.casefold()]
        if len(matches) != 1:
            raise ValueError(f"Label '{label}' matched {len(matches)} UI Automation controls")
        mapped.append(matches[0])
    if dry_run:
        return {
            "status": "planned",
            "window": window.title,
            "labels": labels,
            "seconds": round(time.perf_counter() - started, 3),
        }
    win32.activate(window.handle)
    clicked: list[str] = []
    for label, target in zip(labels, mapped, strict=True):
        if win32.select_window(None).handle != window.handle:
            return {"status": "stopped", "error": "The foreground window changed during the batch", "clicked": clicked}
        if win32.rect_of(window.handle) != current.rect:
            return {"status": "stopped", "error": "The window moved during the batch", "clicked": clicked}
        try:
            win32.click(*target.center)
        except Exception as exc:
            return {"status": "uncertain", "error": str(exc), "clicked": clicked, "attempted": label}
        clicked.append(label)
        time.sleep(delay)
    try:
        final = observe_desktop()
    except Exception as exc:
        return {"status": "uncertain", "error": str(exc), "clicked": clicked}
    options = {"api_key": provider.key, "timeout": 25.0}
    if provider.base_url:
        options["base_url"] = provider.base_url
    if provider.model:
        options["model"] = provider.model
    try:
        with TypeSafeClient(**options) as client:
            response = client.system_one(
                state={
                    "goal": goal,
                    "window": final.window.title,
                    "visible_text": _visible_text(final),
                    "clicked_labels": clicked,
                },
                questions={
                    "verdict": Choice(
                        instructions="Judge the CURRENT screen only. A draft is not proof of submission.",
                        criteria={
                            "achieved": "The requested result is visibly complete.",
                            "not_achieved": "The requested result is not visibly complete.",
                        },
                    ),
                    "proof": Choice(instructions="Choose visible proof, or none.", criteria=_evidence(final)),
                },
            )
    except Exception as exc:
        return {"status": "uncertain", "error": str(exc), "clicked": clicked}
    proof = response.answers["proof"].choice
    evidence = _evidence(final).get(proof) if proof != "none" else None
    achieved = response.answers["verdict"].choice == "achieved" and proof != "none" and bool(evidence)
    return {
        "status": "completed" if achieved else "not_achieved",
        "window": window.title,
        "clicked": clicked,
        "evidence": evidence,
        "jevCalls": 1,
        "seconds": round(time.perf_counter() - started, 3),
    }
