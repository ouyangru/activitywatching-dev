[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8765,
    [string]$Python = "python",
    [switch]$KeepExistingToken
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location -LiteralPath $ProjectRoot

try {
    & $Python -c "import fastapi, uvicorn" 2>$null
} catch {
    throw "当前 Python 缺少依赖。请先运行：$Python -m pip install -r backend/requirements.txt"
}
if ($LASTEXITCODE -ne 0) {
    throw "当前 Python 缺少依赖。请先运行：$Python -m pip install -r backend/requirements.txt"
}

if (-not $KeepExistingToken -or -not $env:ACTIVITYWATCH_API_TOKEN) {
    $bytes = New-Object byte[] 32
    [Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    $env:ACTIVITYWATCH_API_TOKEN = [Convert]::ToHexString($bytes).ToLowerInvariant()
}

if (-not $env:ACTIVITYWATCH_DB_PATH) {
    $dataDir = Join-Path $env:LOCALAPPDATA "ActivityTimeline"
    New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
    $env:ACTIVITYWATCH_DB_PATH = Join-Path $dataDir "activitywatch.db"
}
$env:ACTIVITYWATCH_TIMEZONE = if ($env:ACTIVITYWATCH_TIMEZONE) { $env:ACTIVITYWATCH_TIMEZONE } else { "Asia/Shanghai" }

$lanAddress = Get-NetIPConfiguration |
    Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq "Up" } |
    ForEach-Object { $_.IPv4Address.IPAddress } |
    Where-Object { $_ -and $_ -notlike "169.254.*" } |
    Select-Object -First 1

if (-not $lanAddress) {
    $lanAddress = "<电脑局域网 IP>"
}

$mobileUrl = "http://${lanAddress}:$Port/mobile?token=$($env:ACTIVITYWATCH_API_TOKEN)"
Write-Host ""
Write-Host "行迹手机状态页已准备启动" -ForegroundColor Green
Write-Host "手机与电脑连接同一 Wi-Fi 后，打开：" -ForegroundColor Cyan
Write-Host $mobileUrl -ForegroundColor Yellow
Write-Host ""
Write-Host "如果手机无法连接，请允许 Python 通过 Windows 防火墙的专用网络访问。"
Write-Host "令牌只在本次 PowerShell 进程中有效；停止服务后不会写入仓库。"
Write-Host "按 Ctrl+C 停止服务。"
Write-Host ""

& $Python -m uvicorn backend.app.main:app --host 0.0.0.0 --port $Port
