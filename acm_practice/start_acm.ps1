$ErrorActionPreference = "Continue"

Set-Location $PSScriptRoot

Write-Host "Starting ACM Practice Judge..."
Write-Host "Directory: $(Get-Location)"
Write-Host "Python:"
python --version
Write-Host "Launching server..."

python -u .\server.py --host 127.0.0.1 --port 8765

Write-Host "Server process exited. Check the error above."
Read-Host "Press Enter to close"
