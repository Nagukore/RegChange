<#
.SYNOPSIS
    Writes an API key into backend/.env without it appearing on screen,
    in shell history, or in any transcript.

.DESCRIPTION
    Prompts for the value as a SecureString, so the characters are never
    echoed to the terminal and never land in PSReadLine history. The value is
    held in memory only long enough to rewrite the matching line in
    backend/.env, then the unmanaged buffer is zeroed.

    backend/.env is gitignored. Nothing here prints the secret back.

.EXAMPLE
    & .\scripts\set-api-key.ps1
    & .\scripts\set-api-key.ps1 -Name GEMINI_API_KEY

.NOTES
    TinyFish keys:  https://agent.tinyfish.ai/api-keys
    Gemini keys:    https://aistudio.google.com/apikey
#>
[CmdletBinding()]
param(
    [ValidateSet('TINYFISH_API_KEY', 'GEMINI_API_KEY', 'DATABASE_URL')]
    [string]$Name = 'TINYFISH_API_KEY',

    # Defaults to backend/.env next to this script's repository root.
    [string]$EnvFile
)

$ErrorActionPreference = 'Stop'

if (-not $EnvFile) {
    $repoRoot = Split-Path -Parent $PSScriptRoot
    $EnvFile = Join-Path $repoRoot 'backend\.env'
}

if (-not (Test-Path $EnvFile)) {
    $example = $EnvFile + '.example'
    if (Test-Path $example) {
        Copy-Item $example $EnvFile
        Write-Host "Created $EnvFile from .env.example" -ForegroundColor DarkGray
    }
    else {
        New-Item -ItemType File -Path $EnvFile | Out-Null
        Write-Host "Created empty $EnvFile" -ForegroundColor DarkGray
    }
}

Write-Host ""
Write-Host "Paste the value for $Name (input is hidden, then press Enter):" -ForegroundColor Cyan
$secure = Read-Host -AsSecureString

# Marshal out of the SecureString only for as long as the rewrite needs it.
$bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $plain = [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
}
finally {
    [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
}

if ([string]::IsNullOrWhiteSpace($plain)) {
    Write-Host "No value entered. Nothing was changed." -ForegroundColor Yellow
    return
}

$plain = $plain.Trim()

# Reject a value that was pasted with its own variable name, e.g.
# "TINYFISH_API_KEY=tf_abc" -- keep only the part after the first '='.
if ($plain -like "$Name=*") {
    $plain = $plain.Substring($Name.Length + 1).Trim()
}

$lines = @(Get-Content -Path $EnvFile -Encoding UTF8 -ErrorAction SilentlyContinue)
$replaced = $false
$out = New-Object System.Collections.Generic.List[string]

foreach ($line in $lines) {
    if ($line -match "^\s*$Name\s*=") {
        $out.Add("$Name=$plain")
        $replaced = $true
    }
    else {
        $out.Add($line)
    }
}
if (-not $replaced) { $out.Add("$Name=$plain") }

Set-Content -Path $EnvFile -Value $out -Encoding UTF8

# Report only what can be said safely: that it is set, and how long it is.
$len = $plain.Length
$plain = $null
[System.GC]::Collect()

Write-Host ""
Write-Host "$Name written to $EnvFile ($len characters)." -ForegroundColor Green
Write-Host "The value was not displayed and is not in your shell history." -ForegroundColor DarkGray
Write-Host ""
Write-Host "Restart the API to pick it up:" -ForegroundColor Cyan
Write-Host "  cd backend; .\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000" -ForegroundColor DarkGray
