$ErrorActionPreference = 'Stop'
Write-Host '1 - Vercel AI Gateway (default)'
Write-Host '2 - TypeSafe direct'
$choice = Read-Host 'Provider [1]'
if ($choice -notin @('', '1', '2')) { throw 'Choose 1 or 2.' }
$provider = if ($choice -eq '2') { 'typesafe' } else { 'vercel' }
$secret = Read-Host 'API key (hidden)' -AsSecureString
if ($secret.Length -lt 20) { $secret.Dispose(); throw 'The key is empty or too short.' }
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
$bytes = $null
try {
    $bytes = [Text.Encoding]::UTF8.GetBytes([Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer))
    $protected = [Security.Cryptography.ProtectedData]::Protect($bytes, $null, [Security.Cryptography.DataProtectionScope]::CurrentUser)
    $folder = Join-Path $PSScriptRoot 'config'
    New-Item -ItemType Directory -Path $folder -Force | Out-Null
    [IO.File]::WriteAllBytes((Join-Path $folder 'key.dpapi'), $protected)
    [IO.File]::WriteAllText((Join-Path $folder 'provider.json'), ('{"provider":"' + $provider + '"}'))
    Write-Host 'Key saved with Windows DPAPI for this Windows account.' -ForegroundColor Green
} finally {
    if ($bytes) { [Array]::Clear($bytes, 0, $bytes.Length) }
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    $secret.Dispose()
}
