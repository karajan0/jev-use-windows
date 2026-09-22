"""JSON-only command interface for shell-capable agents."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from . import ocr, win32
from .credentials import Provider, load
from .desktop import observe
from .runner import batch, run

ROOT = Path(__file__).resolve().parents[1]


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(prog="jev.ps1", description="Jev Use for Windows")
    sub = command.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="check the key, OCR, and interactive Windows desktop without an API request")
    sub.add_parser("windows", help="list visible top-level windows")
    inspect = sub.add_parser("inspect", help="read one window's local text and targets without an API request")
    inspect.add_argument("--window", help="unique title substring or #window-handle; default: foreground window")
    loop = sub.add_parser("run", help="let Jev observe and act until the goal is verified or the limit is reached")
    loop.add_argument("goal")
    loop.add_argument("--window", help="unique substring of a visible window title; default: foreground window")
    loop.add_argument("--text", help="exact text supplied by the user for one empty field")
    loop.add_argument("--field", help="required field label substring for --text")
    loop.add_argument(
        "--fill", action="append", default=[], metavar="LABEL=TEXT", help="repeat for several exact field labels"
    )
    loop.add_argument("--url", help="exact HTTPS URL to open in a browser window")
    loop.add_argument("--ocr-language", help="installed Windows OCR language tag, such as en-US or ko")
    loop.add_argument("--steps", type=int, default=12)
    loop.add_argument("--min-confidence", type=float, default=0.35)
    stable = sub.add_parser("batch", help="click exact labels in a stable panel, then verify once with Jev")
    stable.add_argument("goal")
    stable.add_argument("--window", required=True)
    stable.add_argument("--label", action="append", required=True)
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


def execute(args: argparse.Namespace) -> dict | list[dict]:
    win32.enable_dpi_awareness()
    if args.command == "windows":
        return [asdict(item) for item in win32.windows()]
    if args.command == "inspect":
        screen = observe(win32.select_window(args.window))
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
        )
    if not args.goal.strip() or not args.label or any(not item.strip() for item in args.label):
        raise ValueError("A goal and nonempty labels are required")
    provider = Provider("") if args.dry_run else load(ROOT)
    return batch(args.goal, win32.select_window(args.window), provider, args.label, dry_run=args.dry_run)


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
        print(json.dumps({"status": "stopped", "error": "Interrupted by the user"}))
        return 130
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
