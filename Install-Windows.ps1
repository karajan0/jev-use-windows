$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$launcher = if (Get-Command py.exe -ErrorAction SilentlyContinue) { 'py.exe' } elseif (Get-Command python.exe -ErrorAction SilentlyContinue) { 'python.exe' } else { throw 'Install Python 3.12 or newer.' }
if ($launcher -eq 'py.exe') {
    & py.exe -3 -c 'import sys; assert sys.version_info >= (3, 12)'
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 or newer is required.' }
    & py.exe -3 -m venv .venv
} else {
    & python.exe -c 'import sys; assert sys.version_info >= (3, 12)'
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 or newer is required.' }
    & python.exe -m venv .venv
}
if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python virtual environment.' }
& '.\.venv\Scripts\python.exe' -m pip install --disable-pip-version-check -e .
if ($LASTEXITCODE -ne 0) { throw 'Could not install Jev Use for Windows.' }
Write-Host 'Installed. Run .\Set-Key.ps1, then .\jev.ps1 doctor.' -ForegroundColor Green
