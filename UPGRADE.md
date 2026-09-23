# Local runtime upgrade

Applied on 2026-09-23 to the installed Python runtime.

## Changes

- Batch clicks validate the live element's role, name, rectangle, enabled state,
  visibility and runtime ID. A changed target stops the batch before input.
- Completion evidence uses non-editable OCR phrases instead of arbitrary screen
  text. This reduces draft-based false positives; it does not prove delivery for
  every application, particularly when its accessibility tree omits editors.
- Missing-value requests remain available even when other values were supplied.
- Each OCR worker reuses its language engines and asyncio runner. Resources can
  be released using `ocr.close_worker()` on their owning thread.
- Run and batch responses include `goal_achieved` and task-local `timings`.
  Run timings cover capture, UIA, OCR, observation, decision and action paths.
  Nested/parallel timings overlap and must not be summed as wall-clock time.
- Foreground capture also checks window geometry for changes.
- Batch verification rejects low-confidence completion verdicts.
- The local `jev.cmd` now invokes the installed Python CLI; its former JavaScript
  target was missing. This is the Python CLI interface, without HOST_REQUEST support.
- CI runs regression tests. Test discovery excludes unrelated files under runs/.

## Verification

Use `.venv/Scripts/python.exe -m pytest -q`, `-m ruff check .`, and
`-m ruff format --check .`.

The synthetic Windows OCR benchmark alternated baseline and updated reads,
excluded the first iteration, and compared recognized text. In 12 samples per
variant, median time was 31.485 ms before and 27.990 ms after (about 11% lower).
Text matched in every sample. This measures one generated English image on this
machine, not end-to-end application performance or a general speed guarantee.

Run `benchmarks/ocr_runtime.py --baseline PATH_TO_ORIGINAL_OCR_PY` to repeat.

## Tested second upgrade

- Live capture now copies only the foreground region using GDI BitBlt. Pixel
  coordinates remain in virtual-desktop coordinates, including negative origins.
- UIA reads bulk cached properties and direct children, with the existing depth
  and node traversal limits. It never requests a complete cached subtree. An
  unsupported cache initialization falls back to individual property reads.
- Observation runs in a reusable child process per task. `--observation-timeout`
  (default 5 seconds) terminates a stuck worker. Later calls can create a fresh
  worker. This covers observation, not UIA input-action timeouts.
- Fixed post-action sleeps became bounded pixel-change waits. OCR and model
  calls are unnecessary while waiting. This is polling, not UIA event subscription.
- Uniquely named, explicitly supplied input fields execute locally and still
  require readback. Ambiguous names remain model decisions. Model context also
  describes which exact-value input actions are available.
- A success phrase already present when new input is supplied cannot prove the
  new input was saved. The phrase must be absent in an observed state before
  becoming completion evidence. Apps that keep a permanent success label should
  use another changing completion signal, such as `--expect-file`.
- Native UIA targets that fail revalidation can no longer fall back to pixel
  clicks. Foreground identity and geometry are also checked after observation.
- A named Windows session mutex prevents simultaneous Jev input tasks.
- `batch --labels` accepts a JSON array. With PowerShell calling `jev.cmd`, use
  `--labels '[\"Reset\",\"Save\"]'` to preserve quotes through cmd.exe; repeated
  `--label` arguments avoid that shell quoting issue.
- OCR engines are closed on their owning thread when the observer exits normally.

### Results on the dedicated Windows Forms fixture

| Check | Result |
| --- | --- |
| Regression suite | 40 passed |
| Lint and format | Passed |
| Capture comparison, 12 samples per backend | Pillow median 121.493 ms; region GDI 12.422 ms; identical pixels |
| UIA comparison, 12 samples per mode | Individual median 171.700 ms; cached 167.541 ms; identical 7 targets and 57 text entries |
| Repeated captures | 100; GDI resource count unchanged (0 before and after) |
| English field input then Save | `goal_achieved: true`; 1 Jev call; 1.677 seconds |
| Korean field input then Save | `goal_achieved: true`; field readback; 1 Jev call; 1.663 seconds |
| Reset then Save batch | `goal_achieved: true`; 1 Jev call; 1.463 seconds |
| Hung observer and recovery | Timed out, terminated, then a fresh worker succeeded |
| Concurrent input | A second process was blocked until the owner released its mutex |

The capture improvement is about 90% in this fixture. UIA improvement was small
and is not treated as a significant general speedup. The end-to-end timings are
individual successful runs, not a statistical before/after comparison.

Tests also cover changed/disabled/offscreen/replaced targets, negative capture
coordinates, foreground switches, changed OCR bands, stale completion evidence,
ambiguous fields, malformed label arrays and low-confidence batch verdicts.

Reproduction tools: `benchmarks/uia_runtime.py`, `benchmarks/capture_runtime.py`,
`benchmarks/capture_resources.py` and `tests/fixtures/windows_form.ps1`.

### Remaining boundaries

No cross-command persistent service or UIA event subscriptions were introduced.
Mixed-DPI, disconnected RDP and GPU-heavy apps still need separate hardware/app
coverage. UIA actions themselves can still block in an unresponsive provider.
The observer timeout stops the task rather than automatically replaying actions.
Editable regions omitted by an application's UIA tree remain a limitation of
OCR completion classification. Whole-subtree caching is deliberately avoided.

Microsoft API reference used for cache implementation:
https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-cachingforclients

No repository push or release was performed. Original runtime sources and the
launcher were copied to `../JevBackups/upgrade-20260923-200837` before editing.
The second upgrade has a separate backup at `../JevBackups/tested-20260923-201454`.

## Complex-screen reinforcement

This extends the same `run` command; there is no game-specific mode.

- Background pixels no longer count as semantic progress in the repetition guard.
  Transitions distinguish state changes, target-region changes, remote background
  changes and unverified visual changes. None of these alone proves completion.
- Post-action waits inspect the target neighborhood, ignoring remote animation,
  and require two locally stable changed samples when a target region is supplied.
- A stale target is reobserved and reacquired once before input. Native controls
  require the same runtime ID; OCR/visual targets require unique labels plus a
  distinctive image match. Failed or partially dispatched actions are not replayed.
- `--visual-target LABEL=IMAGE` supplies named local reference images for icons
  and canvas elements not exposed through UIA/OCR. Local OpenCV matching searches
  at 0.75x, 1x, 1.25x and 1.5x scales, verifies color and rejects ambiguous matches.
  Up to 16 opaque references, 8–512 pixels per side, are supported.
- `--drag SOURCE=DESTINATION` adds an explicit gesture between two uniquely named
  visible targets. Both endpoints are revalidated. Default duration is 300 ms.
- `--hold KEYS=MILLISECONDS` supplies a bounded hold or chord, for example
  `space=200` or `ctrl+shift+a=250`. Durations are 10–2000 ms. Exceptions and focus
  loss trigger release attempts. Already-held user keys/buttons are left alone.
- Visible UIA Text/StatusBar labels outside editable ancestors can prove completion
  alongside OCR. This fixed a real `Held` versus `HeId` OCR error without fuzzy
  completion matching. Editor/document descendants are excluded from this proof path.
- Model context includes supplied gesture parameters and target positions.

### Verified results

- 70 regression tests passed; lint, formatting and `pip check` passed.
- Synthetic matching: 24/24 moving, scaled and animated-background cases passed,
  including four duplicate-icon rejections. Search median improved from 163.209 ms
  to 27.710 ms after grayscale search with color verification (800×500 test frames).
- Real animated Windows canvas: a moving reference icon was reacquired before
  clicking; `goal_achieved: true`, one Jev call, 1.503 seconds.
- Real canvas drag: `goal_achieved: true`, one Jev call, 2.401 seconds.
- Real 200 ms Space hold: `goal_achieved: true`, one Jev call, 1.127 seconds.
- Tests cover occlusion, duplicate icons, moved/replaced controls, scale changes,
  negative screen coordinates, animated no-op clicks, editor proof exclusion,
  interrupted chords, partial input failures and interrupted drags.

These are synthetic/dedicated-fixture results, not general application success rates.
The real drag run preceded the image-search optimization; its timing is not a
before/after comparison. Known-reference matching does not infer the meaning of
unseen icons, handle arbitrary rotations, or prove text-free task completion.
Pixel/semantic change classification is heuristic, not a learned scene model.
Forced OS process termination cannot guarantee that a finally-based release runs.

### Examples

```powershell
jev.cmd run 'Click the launch icon and verify completion' --window 'My App' `
  --visual-target 'launch=C:\references\launch.png' --expect-visible 'Completed'

jev.cmd run 'Drag source onto destination' --window 'My App' `
  --visual-target 'source=C:\references\source.png' `
  --visual-target 'destination=C:\references\destination.png' `
  --drag 'source=destination' --expect-visible 'Moved'

jev.cmd run 'Hold Space for the supplied duration' --window 'My App' `
  --hold 'space=200' --expect-visible 'Completed'
```

Reproduce the image benchmark with `benchmarks/visual_runtime.py`; it generates
the reference assets for `tests/fixtures/visual_form.ps1`. References and captured
images remain local. Algorithm reference:
https://docs.opencv.org/4.x/d4/dc6/tutorial_py_template_matching.html

Backup before this phase: `../JevBackups/visual-20260923-203934`.

## Research-driven reliability and observation reuse

The repeat-input guard now uses an action-scoped state: native/visual controls
and nearby OCR, excluding passive remote text and absolute target geometry.
Clock ticks, subtitle changes and target movement alone cannot authorize sending
the same input again. Native control values, identities, focus and nearby OCR
remain significant. Completion checks run independently before this guard.
This is deliberately conservative: workflows requiring repeated clicks with no
observable control-state change may stop. It is not a general goal-state model.

Generic model-selected completion now also checks pending supplied text actions,
URLs and gestures, using the same input guard as explicit postconditions.

Visual matching shares frame RGB/grayscale preparation across references and
caches scaled template preprocessing (32 entries). A thread-local cache retains
up to 16 match results for the last exact frame, including negative results.
It stores no screenshots. Frame dimensions/pixels and template content identify
cached results; screen offsets are applied after lookup. Changed pixels force a
full search, preserving detection of new duplicates anywhere in the frame.
Nearby-only search is not enabled because it needs additional identity evidence
before it can safely replace this global ambiguity check.

Validation on 2026-09-23:

- 77 unit/regression tests pass, including remote clocks/subtitles, moving
  targets, pending-input completion, cache invalidation on movement/removal/new
  duplicates, reference changes and negative screen coordinates.
- Original 24-case synthetic visual benchmark: 24/24 correct, including all
  four duplicate rejections; median 28.451 ms.
- Paired baseline comparison, two references at 1080x720, 24 cases per scenario:
  unchanged frames median 111.498 -> 7.038 ms, p95 150.331 -> 7.543 ms;
  moving frames median 138.334 -> 138.543 ms, p95 154.121 -> 156.363 ms.
  All 48 target-result lists exactly matched the baseline. Order alternated.
  These measure local image matching, not whole-task or real-app speedups.
  No dynamic-frame speed improvement was established.

Reproduce with `benchmarks/visual_cache_runtime.py --baseline PATH_TO_OLD_VISUAL_PY`.
Backup: `../JevBackups/research-20260923-210726`.

Research context: [ScreenSpot-Pro](https://arxiv.org/abs/2504.07981) motivates
selective spatial observation; [OSWorld](https://arxiv.org/abs/2404.07972)
motivates independent task-result evaluation. Conditional multi-action plans,
unseen-icon models and reusable workflow memory remain future work and are not
part of this update.

## High-resolution grounding repair

The installed runtime now uses magnified overlapping OCR tiles for images larger
than 1800 pixels, retains late-screen OCR candidates, ranks a bounded decision
context by the goal with spatial coverage, proposes small geometric controls near
text, and selects one concrete action per model decision. See
[the repair report](benchmarks/GROUNDING_REPAIR_REPORT.md) for evaluation protocol,
raw result paths and limitations. Hard subset accuracy improved from 0/32 to 4/32;
an image-disjoint held-out subset improved from 1/32 to 6/32. High-resolution
observation costs increased to roughly 1.17s median on these samples.

84 regression tests pass. Three actual `jev.cmd run` fixture tasks completed,
including a small unlabeled X control without a supplied reference image.

## Verified fast workflows

Native value setting/readback, validated chunks of explicit fields, ordered
`--click` labels, immediate observation before waiting, and exact OCR tile reuse
are now enabled. A 12-field live fixture improved from 11.142s to 4.186s median
CLI wall time across three paired runs, with successful completion in all six
runs and model calls reduced from one to zero in the explicit workflow.
96 tests pass. [Full measurements and limits](benchmarks/SPEED_REPORT.md).
