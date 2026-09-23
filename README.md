# Jev Use for Windows

A PowerShell-first Windows desktop control tool for AI agents, powered by
[Jev](https://docs.typesafe.ai/). It captures the live Windows desktop, reads
the current foreground window through OCR and UI Automation, performs mouse
and keyboard actions, and returns the result as JSON.

## Fast explicit workflows

Use exact supplied field names and button labels to avoid model selection for
known workflows. Native fields that are already visible are filled together,
with live identity checks and exact value readback for every field. Buttons are
resolved and verified again when their turn arrives, including newly opened dialogs.

```powershell
jev.cmd run 'Fill the form and save' --window 'My Form' `
  --fill 'Name=Example' --fill 'City=Seoul' `
  --click 'Save' --expect-visible 'Saved successfully'
```

Repeat `--click` for an ordered sequence after the supplied fields. Missing or
ambiguous labels stop the sequence. Native value providers avoid clipboard races;
unsupported fields keep the normal input path. Completion is still verified.
See [measured performance and limitations](benchmarks/SPEED_REPORT.md).

## Requirements

- Windows 10 or Windows 11
- Python 3.12 or newer
- An interactive Windows desktop
- A TypeSafe Jev API key or a Vercel AI Gateway key with Jev access

## Install

Open PowerShell and run:

```powershell
Set-Location $env:USERPROFILE
git clone https://github.com/karajan0/jev-use-windows.git
Set-Location .\jev-use-windows
Set-ExecutionPolicy -Scope Process Bypass -Force
.\Install-Windows.ps1
```

The installer creates `.venv` inside the repository and installs the runtime.

## Add an API key

Run:

```powershell
.\Set-Key.ps1
```

Choose Vercel AI Gateway or TypeSafe direct, then enter the key in the hidden
prompt. The key is protected with Windows DPAPI for the current Windows account.

| File | Content |
| --- | --- |
| `config\provider.json` | Selected provider name |
| `config\key.dpapi` | DPAPI-protected API key |

You can use `TYPESAFE_API_KEY` or `AI_GATEWAY_API_KEY` environment variables
instead. Environment variables take precedence over the saved key.

Check the installation:

```powershell
.\jev.ps1 doctor
```

## Install the Codex skill

Run:

```powershell
.\Install-Skill.ps1
```

This installs the skill at:

```text
C:\Users\<your-Windows-user>\.agents\skills\jev-use-windows\SKILL.md
```

Start a new Codex session if the skill does not appear immediately. You can
then invoke it directly:

> `$jev-use-windows` Calculate 1283 × 2346 in the open Calculator window.

The repository also contains the skill at
`.agents\skills\jev-use-windows\SKILL.md`, so Codex can discover it while
working inside this repository.

## Use from PowerShell

### List visible windows

```powershell
.\jev.ps1 windows
```

Each result includes a title, window handle, and screen coordinates. Use a
unique part of the title with `--window`, or use `#HANDLE` when titles are
ambiguous.

### Run an adaptive task

```powershell
.\jev.ps1 run 'Clear the calculator display to 0' --window 'Calculator'
```

`run` brings the selected window forward once, then captures the live desktop
and follows whichever window is in front after each action. New app windows and
save dialogs become the next observation. Jev chooses each action from the
current foreground window until the goal is completed or a stop condition is
reached. It chooses the action kind and a compatible target in one Jev request.
Editable combo boxes and explicitly named fields can receive supplied text.
After typing, the worker reads the focused field's value when Windows exposes
it; otherwise it checks the next visible screen state without repeating the
input automatically.

Supply exact text or URLs with the command:

```powershell
.\jev.ps1 run 'Enter the note and save it' `
  --window 'Notes' `
  --text 'Exact note' `
  --field 'Note'

.\jev.ps1 run 'Fill and submit the form' `
  --window 'Customer form' `
  --fill 'Name=Alex' `
  --fill 'City=Seoul'

.\jev.ps1 run 'Open the supplied page' `
  --window 'Microsoft Edge' `
  --url 'https://example.com'
```

For a task with a known completion signal, require it before returning
`completed`:

```powershell
.\jev.ps1 run 'Save the report as Report.hwp' `
  --window 'Hancom Office' `
  --fill 'File name=Report.hwp' `
  --expect-file "$env:USERPROFILE\Documents\Report.hwp"

.\jev.ps1 run 'Submit the form' `
  --window 'Customer form' `
  --fill 'Name=Alex' `
  --expect-visible 'Saved: Alex'
```

`--expect-file` requires a file to be created or changed during the run.
`--expect-visible` matches one OCR line or nearby phrase outside editable
fields, so a draft value alone cannot satisfy it. Both conditions must pass
when both are supplied.

### Run a known button sequence

Use `batch` when every button is already visible and its exact label is known:

```powershell
.\jev.ps1 batch 'Apply and save this record' `
  --window 'Inventory' `
  --label 'Apply' `
  --label 'Save'
```

Validate the labels before clicking:

```powershell
.\jev.ps1 batch 'Apply and save this record' `
  --window 'Inventory' `
  --label 'Apply' `
  --label 'Save' `
  --dry-run
```

### Inspect a window

```powershell
.\jev.ps1 inspect --window 'Inventory'
```

`inspect` returns the text and controls detected through OCR and UI Automation.
Use it to find the exact window and control labels accepted by other commands.

## Commands

| Command | Purpose |
| --- | --- |
| `doctor` | Check Windows, API key availability, OCR languages, windows, and monitors |
| `windows` | List visible top-level windows |
| `inspect` | Read detected text and controls from one window |
| `run` | Let Jev choose and perform actions until the goal is resolved |
| `batch` | Click a supplied sequence of exact UI Automation labels and verify the result |

Run command help with:

```powershell
.\jev.ps1 --help
.\jev.ps1 run --help
.\jev.ps1 batch --help
```

## Result format

Commands print JSON to standard output. A completed task resembles:

```json
{
  "status": "completed",
  "goal": "Save the record",
  "window": "Inventory",
  "actions": ["Click ButtonControl 'Save'"],
  "evidence": "Saved",
  "jevCalls": 2,
  "seconds": 1.42
}
```

Possible task states include:

| Status | Meaning |
| --- | --- |
| `completed` | The current screen contains evidence that the goal was completed |
| `not_achieved` | Completion evidence or a supplied completion condition was missing |
| `needs_input` | The task requires an exact value that was not supplied |
| `blocked` | No available action can advance the task |
| `stalled` | The same action and screen state repeated |
| `uncertain` | Confidence was too low or an action had an uncertain result |
| `step_limit` | The configured action limit was reached |
| `failed` | The task could not start or observation failed |

## How it works

```text
PowerShell command
    -> Python worker
    -> capture the visible desktop
    -> OCR the current foreground window and read its UI Automation controls
    -> Jev action selection
    -> Windows mouse or keyboard input
    -> find the new foreground window and verify the result
    -> JSON response
```

OCR and UI Automation run in parallel. Unchanged regions reuse their OCR
result; changed screen bands are read again. Jev receives text and action
choices; desktop images stay
local. Native buttons use UI Automation invocation when available, with a
screen-coordinate click fallback. Before acting, the worker checks that the
target still looks like the observed screen. Action history marks matching field readback
as `field_readback`, a visible screen change as `visual_change`, and an action with
no visible change as `suspected_noop`; a screen change alone does not prove
task completion. `batch` maps controls before the first click and uses one Jev
call to verify the final screen.

## Desktop support

- Windows 10 and Windows 11
- Foreground window tracking after an explicit title/handle selection
- Negative virtual-desktop coordinates
- Multiple monitors
- Connected RDP sessions

Mixed-DPI monitor layouts have not yet been verified. `inspect` and `batch`
still use a selected-window capture, which some GPU-rendered applications may
not provide reliably.

## Development

See [UPGRADE.md](UPGRADE.md) for local runtime changes, measured results and limitations.

```powershell
python -m pip install -e . ruff pytest
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
```

## License

[MIT](LICENSE)
