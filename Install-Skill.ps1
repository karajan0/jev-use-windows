param(
    [string]$Destination = (Join-Path ([Environment]::GetFolderPath('UserProfile')) '.agents\skills\jev-use-windows'),
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$runtime = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$source = Join-Path $runtime '.agents\skills\jev-use-windows\SKILL.md'
$target = [IO.Path]::GetFullPath($Destination)
$marker = Join-Path $target 'runtime-path.txt'
if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw 'The skill source is missing.' }
if (Test-Path -LiteralPath $target) {
    $existing = if (Test-Path -LiteralPath $marker) { (Get-Content -LiteralPath $marker -Raw).Trim() } else { '' }
    if (-not $Force -and $existing -ne $runtime) { throw "Another skill installation already uses $target. Use -Force to replace it." }
}
New-Item -ItemType Directory -Path $target -Force | Out-Null
Copy-Item -LiteralPath $source -Destination (Join-Path $target 'SKILL.md') -Force
[IO.File]::WriteAllText($marker, $runtime, [Text.UTF8Encoding]::new($false))
Write-Host "Installed jev-use-windows in $target" -ForegroundColor Green
