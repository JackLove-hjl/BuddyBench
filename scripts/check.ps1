# One-shot self check: backend tests -> frontend type check -> frontend build.
#
# Usage (from anywhere):
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check.ps1
#
# Notes:
# - Keep every message ASCII: PowerShell 5.1 reads .ps1 as ANSI and would mangle Chinese.
# - Do NOT set $ErrorActionPreference = 'Stop': native commands (uv / npm) write progress
#   to stderr, which PowerShell turns into error records and would abort the script.
#   We check $LASTEXITCODE instead.

$root = Split-Path -Parent $PSScriptRoot
$failed = @()

Write-Host ''
Write-Host '=== [1/3] backend tests (uv run pytest) ===' -ForegroundColor Cyan
Push-Location (Join-Path $root 'backend')
uv run pytest
if ($LASTEXITCODE -ne 0) { $failed += 'backend tests' }
Pop-Location

Write-Host ''
Write-Host '=== [2/3] frontend type check (vue-tsc) ===' -ForegroundColor Cyan
Push-Location (Join-Path $root 'web')
npx vue-tsc -b --pretty false
if ($LASTEXITCODE -ne 0) { $failed += 'vue-tsc' }

Write-Host ''
Write-Host '=== [3/3] frontend build (vite) ===' -ForegroundColor Cyan
npm run build
if ($LASTEXITCODE -ne 0) { $failed += 'vite build' }
Pop-Location

Write-Host ''
if ($failed.Count -gt 0) {
    Write-Host ('FAILED: ' + ($failed -join ', ')) -ForegroundColor Red
    exit 1
}
Write-Host 'ALL CHECKS PASSED' -ForegroundColor Green
exit 0
