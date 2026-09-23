"""Read-only UIA comparison against the dedicated regression fixture."""

import json
import statistics
import time

from jev_use import desktop, win32


def main():
    win32.enable_dpi_awareness()
    window = win32.select_window("Jev Regression Fixture")
    samples = {False: [], True: []}
    expected = None
    for iteration in range(13):
        for cached in [False, True] if iteration % 2 else [True, False]:
            started = time.perf_counter()
            targets, names = desktop._uia_targets(window.handle, window.rect, cached=cached)
            elapsed = time.perf_counter() - started
            signature = [(item.kind, item.label, item.rect, item.value, item.focused) for item in targets], names
            if expected is None:
                expected = signature
            if signature != expected or not any(item.label == "Save" for item in targets):
                raise RuntimeError("UIA results differ or fixture is incomplete")
            if iteration:
                samples[cached].append(elapsed)
    print(
        json.dumps(
            {
                "same_targets_and_text": True,
                "target_count": len(expected[0]),
                "name_count": len(expected[1]),
                "timings": {
                    "cached" if key else "individual": {
                        "median_ms": round(statistics.median(values) * 1000, 3),
                        "samples": len(values),
                    }
                    for key, values in samples.items()
                },
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
