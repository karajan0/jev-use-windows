$ErrorActionPreference = 'Stop'
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Run .\Install-Windows.ps1 first.' }
& $python -X utf8 -m jev_use.cli @args
exit $LASTEXITCODE
