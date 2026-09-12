$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

Write-Host "Starting ACM Practice Judge..."
Write-Host "Directory: $(Get-Location)"

python3 .\server.py --host 127.0.0.1 --port 8765

Read-Host "Press Enter to close"
