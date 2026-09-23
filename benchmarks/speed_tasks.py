"""Actual jev.cmd A/B runs against a fixture that validates all 12 field values."""

import argparse
import json
import os
import statistics
import subprocess
import time
from pathlib import Path

from jev_use import credentials, win32


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=3)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    provider = credentials.load(root)
    env = dict(os.environ)
    # Pass the existing credential only in the child environment, never argv/logs.
    if provider.base_url is None:
        env["TYPESAFE_API_KEY"] = provider.key
    else:
        raise RuntimeError("This A/B launcher currently supports the configured direct Jev provider only")
    command = Path.home() / ".local" / "bin" / "jev.cmd"
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    rows = []
    for iteration in range(args.rounds):
        versions = ("baseline", "updated") if iteration % 2 == 0 else ("updated", "baseline")
        for version in versions:
            helper = subprocess.Popen(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(root / "tests" / "fixtures" / "speed_form.ps1"),
                ],
                startupinfo=startup,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                deadline = time.monotonic() + 10
                while not any(w.title == "Jev Speed Fixture" for w in win32.windows()):
                    if helper.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError("Speed fixture failed to start")
                    time.sleep(0.05)
                goal = "Fill all supplied fields and click Save. Verify Saved 12 fields."
                argv = [
                    str(command),
                    "run",
                    goal,
                    "--window",
                    "Jev Speed Fixture",
                    "--expect-visible",
                    "Saved 12 fields",
                    "--steps",
                    "20",
                ]
                for index in range(1, 13):
                    argv += ["--fill", f"Field{index:02}=VALUE{index:02}"]
                if version == "updated":
                    argv += ["--click", "Save"]
                started = time.perf_counter()
                completed = subprocess.run(
                    argv,
                    cwd=args.baseline if version == "baseline" else root,
                    env=env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=90,
                )
                wall = time.perf_counter() - started
                result = json.loads(completed.stdout.strip())
                rows.append(
                    {"version": version, "iteration": iteration, "wall_seconds": round(wall, 3), "result": result}
                )
                args.output.write_text(json.dumps({"runs": rows}, indent=2, ensure_ascii=False), encoding="utf-8")
                print(
                    f"{version} {iteration}: goal={result.get('goal_achieved')} "
                    f"wall={wall:.3f}s jev={result.get('jevCalls')}",
                    flush=True,
                )
                if not result.get("goal_achieved"):
                    raise RuntimeError("Task failed; results saved, no automatic retry")
            finally:
                if helper.poll() is None:
                    helper.terminate()
                    helper.wait(timeout=5)
    summary = {}
    for version in ("baseline", "updated"):
        group = [row for row in rows if row["version"] == version]
        summary[version] = {
            "successful": len(group),
            "wall_median_seconds": statistics.median(row["wall_seconds"] for row in group),
            "runtime_median_seconds": statistics.median(row["result"]["seconds"] for row in group),
            "model_calls": [row["result"]["jevCalls"] for row in group],
        }
    args.output.write_text(
        json.dumps({"summary": summary, "runs": rows}, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
