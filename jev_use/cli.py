"""JSON-only command interface for shell-capable agents."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from . import ocr, win32
from .credentials import Provider, load
from .observer import Observer
from .runner import batch, run

ROOT = Path(__file__).resolve().parents[1]


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(prog="jev.ps1", description="Jev Use for Windows")
    sub = command.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="check the key, OCR, and interactive Windows desktop without an API request")
    sub.add_parser("windows", help="list visible top-level windows")
    inspect = sub.add_parser("inspect", help="read one window's local text and targets without an API request")
    inspect.add_argument("--window", help="unique title substring or #window-handle; default: foreground window")
    inspect.add_argument("--observation-timeout", type=float, default=5.0)
    inspect.add_argument("--visual-target", action="append", default=[], metavar="LABEL=IMAGE")
    loop = sub.add_parser("run", help="let Jev observe and act until the goal is verified or the limit is reached")
    loop.add_argument("goal")
    loop.add_argument("--window", help="unique substring of a visible window title; default: foreground window")
    loop.add_argument("--text", help="exact text supplied by the user for one field")
    loop.add_argument("--field", help="required field label substring for --text")
    loop.add_argument(
        "--fill", action="append", default=[], metavar="LABEL=TEXT", help="repeat for several exact field labels"
    )
    loop.add_argument("--url", help="exact HTTPS URL to open in a browser window")
    loop.add_argument("--expect-visible", help="exact completion text required outside editable fields")
    loop.add_argument("--expect-file", type=Path, help="output file that must be created or changed during this run")
    loop.add_argument("--ocr-language", help="installed Windows OCR language tag, such as en-US or ko")
    loop.add_argument("--steps", type=int, default=12)
    loop.add_argument("--min-confidence", type=float, default=0.35)
    loop.add_argument("--observation-timeout", type=float, default=5.0)
    loop.add_argument("--visual-target", action="append", default=[], metavar="LABEL=IMAGE")
    loop.add_argument("--drag", action="append", default=[], metavar="SOURCE=DESTINATION")
    loop.add_argument("--hold", action="append", default=[], metavar="KEYS=MILLISECONDS")
    loop.add_argument(
        "--click",
        action="append",
        default=[],
        metavar="EXACT_LABEL",
        help="after supplied fields, click these exact labels in order with live validation",
    )
    stable = sub.add_parser("batch", help="click exact labels in a stable panel, then verify once with Jev")
    stable.add_argument("goal")
    stable.add_argument("--window", required=True)
    labels = stable.add_mutually_exclusive_group(required=True)
    labels.add_argument("--label", action="append")
    labels.add_argument("--labels", help='JSON array of exact labels, e.g. ["Apply", "Save"]')
    stable.add_argument("--observation-timeout", type=float, default=5.0)
    stable.add_argument("--dry-run", action="store_true")
    return command


def _fills(entries: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for entry in entries:
        if "=" not in entry:
            raise ValueError("--fill must be LABEL=TEXT")
        label, value = entry.split("=", 1)
        label = label.strip().casefold()
        if not label or not value.strip() or label in result:
            raise ValueError("--fill needs a unique, nonempty label and value")
        result[label] = value
    return result


def _visual_targets(entries: list[str]) -> dict[str, str]:
    from .visual import load_template

    if len(entries) > 16:
        raise ValueError("At most 16 visual target references are supported")
    result = _fills(entries)
    for label, path in result.items():
        resolved = Path(path).resolve(strict=True)
        load_template(resolved)
        result[label] = str(resolved)
    return result


def _holds(entries):
    from .gestures import key_codes

    result = {}
    for chord, milliseconds in _fills(entries).items():
        key_codes(chord)
        seconds = float(milliseconds) / 1000
        if not 0.01 <= seconds <= 2.0:
            raise ValueError("Hold duration must be 10 to 2000 milliseconds")
        result[chord] = seconds
    return result


def execute(args: argparse.Namespace) -> dict | list[dict]:
    win32.enable_dpi_awareness()
    if args.command == "windows":
        return [asdict(item) for item in win32.windows()]
    if args.command == "inspect":
        references = _visual_targets(args.visual_target)
        worker = Observer(args.observation_timeout)
        try:
            screen = worker.call("window", win32.select_window(args.window), visual_targets=references)
        finally:
            worker.close()
        return {
            "status": "observed",
            "window": asdict(screen.window),
            "text": screen.text,
            "targets": [asdict(item) for item in screen.targets],
        }
    if args.command == "doctor":
        checks = {"windows": sys.platform == "win32", "apiKey": False, "ocr": False, "visibleWindow": False}
        errors: list[str] = []
        try:
            load(ROOT)
            checks["apiKey"] = True
        except Exception as exc:
            errors.append(str(exc))
        try:
            languages = ocr.available_languages()
            checks["ocr"] = bool(languages)
            checks["visibleWindow"] = bool(win32.windows())
        except Exception as exc:
            errors.append(str(exc))
            languages = []
        return {
            "status": "ready" if all(checks.values()) else "failed",
            "checks": checks,
            "ocrLanguages": languages,
            "monitors": win32.monitors(),
            "errors": errors,
        }
    if args.command == "run":
        if not args.goal.strip() or not 1 <= args.steps <= 100 or not 0 <= args.min_confidence <= 1:
            raise ValueError("Invalid goal, step limit, or confidence")
        if args.field and args.text is None:
            raise ValueError("--field requires --text")
        if args.text is not None and (not args.text.strip() or args.fill):
            raise ValueError("Use either nonempty --text or --fill")
        if args.url and not args.url.startswith("https://"):
            raise ValueError("--url requires HTTPS")
        if args.expect_visible is not None and not args.expect_visible.strip():
            raise ValueError("--expect-visible requires nonempty text")
        if args.expect_file is not None and not args.expect_file.is_absolute():
            raise ValueError("--expect-file requires an absolute path")
        if any(not label.strip() for label in args.click):
            raise ValueError("--click requires nonempty exact labels")
        return run(
            args.goal,
            win32.select_window(args.window),
            load(ROOT),
            text=args.text,
            field=args.field,
            fills=_fills(args.fill),
            url=args.url,
            language=args.ocr_language,
            max_steps=args.steps,
            min_confidence=args.min_confidence,
            expected_visible=args.expect_visible,
            expected_file=args.expect_file,
            observation_timeout=args.observation_timeout,
            visual_targets=_visual_targets(args.visual_target),
            drags=_fills(args.drag),
            holds=_holds(args.hold),
            clicks=args.click,
        )
    labels = args.label if args.label is not None else json.loads(args.labels)
    if (
        not args.goal.strip()
        or not isinstance(labels, list)
        or not labels
        or any(not isinstance(item, str) or not item.strip() for item in labels)
    ):
        raise ValueError("A goal and nonempty labels are required")
    provider = Provider("") if args.dry_run else load(ROOT)
    return batch(
        args.goal,
        win32.select_window(args.window),
        provider,
        labels,
        dry_run=args.dry_run,
        observation_timeout=args.observation_timeout,
    )


def main(argv: list[str] | None = None) -> int:
    try:
        result = execute(parser().parse_args(argv))
        print(json.dumps(result, ensure_ascii=False))
        return (
            0
            if isinstance(result, list) or result.get("status") in {"ready", "observed", "completed", "planned"}
            else 1
        )
    except KeyboardInterrupt:
        print(json.dumps({"status": "stopped", "goal_achieved": False, "error": "Interrupted by the user"}))
        return 130
    except Exception as exc:
        print(json.dumps({"status": "failed", "goal_achieved": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
