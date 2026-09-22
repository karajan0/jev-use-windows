# Install Jev Use for Windows

Run these commands in PowerShell on the same interactive Windows desktop as
the application you want the agent to use. A disconnected RDP session or a
cloud agent without access to that desktop cannot provide live input.

## 1. Install

Install Python 3.12 or newer, then:

```powershell
Set-Location $env:USERPROFILE
git clone https://github.com/karajan0/jev-use-windows.git
Set-Location .\jev-use-windows
Set-ExecutionPolicy -Scope Process Bypass -Force
.\Install-Windows.ps1
```

The installer creates `.venv` in the repository. Keep the repository folder:
the agent skill points to it. On a typical installation the path is
`C:\Users\<your-Windows-user>\jev-use-windows`.

## 2. Add a Jev API key

Run `.\Set-Key.ps1` yourself. Choose Vercel AI Gateway (`1`, default) or
TypeSafe direct (`2`) and enter that provider's key at the hidden prompt.
The files are:

| Path | Content |
| --- | --- |
| `config/provider.json` | Provider name only. |
| `config/key.dpapi` | The key protected with Windows DPAPI for the current account. |

`config/` is excluded by Git. The key is not stored in the skill. After
changing Windows accounts or computers, run `Set-Key.ps1` again. You can
instead set `TYPESAFE_API_KEY` or `AI_GATEWAY_API_KEY` in the agent process
environment. Environment keys take precedence over the saved key, with
`TYPESAFE_API_KEY` first. Do not put an API key in a prompt or a public issue.

## 3. Check the desktop

```powershell
.\jev.ps1 doctor
.\jev.ps1 windows
```

`doctor` reports key availability, Windows OCR languages, visible windows,
and monitor rectangles without making an API request. `windows` lists title
and handle values. Select a unique title substring, or use `#` followed by a
reported handle when several windows have the same title.

## 4. Apply the Codex skill

The repository skill is at `.agents\skills\jev-use-windows\SKILL.md`. To
make it available from other projects, run `.\Install-Skill.ps1`. It copies
the skill to
`C:\Users\<your-Windows-user>\.agents\skills\jev-use-windows\SKILL.md`
and writes `runtime-path.txt` beside it. The API key is not copied. Start a
new Codex session if `/skills` does not show `$jev-use-windows`. After moving
the repository, rerun `Install-Skill.ps1 -Force` from the new location.

## 5. Run a task

```powershell
.\jev.ps1 run 'Open the requested record and save it' --window 'Inventory'
.\jev.ps1 run 'Fill this note and save it' --window 'Inventory' --text 'Exact note' --field 'Note'
.\jev.ps1 inspect --window 'Inventory'
```

`run` needs the exact text or URL supplied up front. It does not invent
missing values. Its JSON result reports `completed`, `needs_input`,
`uncertain`, `stalled`, `blocked`, or `step_limit`. Check `evidence` before
reporting success. Do not automatically retry a partial send or publish.

For controls that are all visible and stable, `batch` accepts repeated
`--label` arguments. Use `--dry-run` to validate labels without clicking.
The worker maps labels locally and asks Jev once to verify the final screen.
Use `run` when controls appear only after earlier actions.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| PowerShell blocks scripts | Run `Set-ExecutionPolicy -Scope Process Bypass -Force` in this session. |
| Python is missing or too old | Install Python 3.12+ and rerun `Install-Windows.ps1`. |
| `doctor` reports no key | Run `Set-Key.ps1` in this Windows account or provide a supported environment variable. |
| Saved key cannot be decrypted | Rerun `Set-Key.ps1` under the Windows account that will run the agent. |
| No visible window | Open an app in a connected interactive Windows session. |
| Title is ambiguous | Run `windows` and select `--window '#HANDLE'`. |
| OCR has no language | Install a Windows OCR language pack in Windows language settings. |
| Skill is missing | Run `Install-Skill.ps1`, then start a new Codex session and check `/skills`. |
