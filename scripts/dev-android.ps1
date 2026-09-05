param(
    # install  = 构建 + 安装 + 启动 App（默认，日常开发用这个）
    # log      = 只看手机端实时日志（不重新构建安装）
    # build    = 只构建 APK，不安装
    # reinstall = 跳过构建，直接安装现有 APK + 启动
    # status   = 检查手机上服务的运行状态
    [ValidateSet("install", "log", "build", "reinstall", "status")]
    [string]$Action = "install",
    [string]$Device = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$AndroidDir = Join-Path $ProjectRoot "android"
$ApkPath = Join-Path $AndroidDir "app\build\outputs\apk\debug\app-debug.apk"
$DebugPackage = "com.ouyangru.activitytimeline.debug"
$MainPackage = "com.ouyangru.activitytimeline"
$LogTag = "ActivityTimeline"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Resolve-Adb {
    # 优先已知 SDK 路径，PATH 兜底：系统里可能存在多个不同版本 adb，
    # 旧版客户端（如厂商刷机工具附带的）与新版 adb server 协议不兼容，会看不到设备
    $candidates = @(
        "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe",
        "$env:ANDROID_HOME\platform-tools\adb.exe",
        "$env:ANDROID_SDK_ROOT\platform-tools\adb.exe",
        "D:\Android\Sdk\platform-tools\adb.exe"
    )
    foreach ($path in $candidates) {
        if (Test-Path $path) { return $path }
    }
    $fromPath = Get-Command adb -ErrorAction SilentlyContinue
    if ($fromPath) { return $fromPath.Source }
    throw "adb not found. Install Android platform-tools or add adb to PATH."
}

function Select-Device {
    param([string]$Adb)
    # @() 强制数组：单设备时管道返回标量字符串，索引 [0] 会取到第一个字符
    $raw = @(& $Adb devices 2>&1)
    $lines = @($raw | Select-Object -Skip 1 | Where-Object { $_ -match "^\S+\s+device\b" })
    if ($lines.Count -eq 0) {
        # 失败时落盘原始输出，便于排查 adb server 版本冲突 / 设备掉线
        try { $raw | Set-Content (Join-Path $PSScriptRoot "dev-android-diag.txt") -Encoding UTF8 } catch {}
        throw "No device connected. Enable USB debugging (Settings > About phone > tap Build number 7x, then Developer options > USB debugging) and reconnect. Raw adb output saved to scripts\dev-android-diag.txt"
    }
    if ($Device) {
        $matched = @($lines | Where-Object { $_ -like "$Device*" })
        if ($matched.Count -eq 0) { throw "Device '$Device' not in connected list: $($lines -join ', ')" }
        return $Device
    }
    # 多条 transport（USB + 无线）指向同一台手机时只取第一个
    $serial = ($lines[0] -split "\s+")[0]
    if ($lines.Count -gt 1) { Write-Host "Multiple transports detected, using: $serial" -ForegroundColor Yellow }
    return $serial
}

function Invoke-Build {
    Write-Step "Building debug APK"
    Push-Location $AndroidDir
    try {
        & .\gradlew.bat assembleDebug --console=plain
        if ($LASTEXITCODE -ne 0) { throw "gradle build failed (exit $LASTEXITCODE)" }
    } finally {
        Pop-Location
    }
    if (-not (Test-Path $ApkPath)) { throw "Build reported success but APK not found at $ApkPath" }
    $size = [math]::Round((Get-Item $ApkPath).Length / 1MB, 1)
    Write-Host "APK ready: $ApkPath ($size MB)"
}

function Invoke-Install {
    param([string]$Adb, [string]$Serial)
    if (-not (Test-Path $ApkPath)) { throw "APK missing. Run with -Action install or build first." }
    Write-Step "Installing APK to device $Serial"
    & $Adb -s $Serial install -r $ApkPath
    if ($LASTEXITCODE -ne 0) { throw "adb install failed" }

    Write-Step "Launching app"
    & $Adb -s $Serial shell am start -n "$DebugPackage/$MainPackage.MainActivity" | Out-Null
    Write-Host "Done. Complete first-run setup in the app:" -ForegroundColor Green
    Write-Host "  1. Grant Usage access permission (button in app)"
    Write-Host "  2. Fill backend URL / token / device name, enable monitoring, save"
    Write-Host "  3. Tap 'ignore battery optimizations' and allow"
    Write-Host "  4. System settings > Battery > Background power > allow for this app"
    Write-Host "  5. Lock the app in Recents"
    Write-Host ""
    Write-Host "Watch live logs:  .\dev-android.ps1 -Action log"
}

$adb = Resolve-Adb

switch ($Action) {
    "build" {
        Invoke-Build
    }
    "install" {
        "resolved adb: [$adb]" | Set-Content (Join-Path $PSScriptRoot "dev-android-diag.txt") -Encoding UTF8
        @(& $adb devices 2>&1) | ForEach-Object { "pre-build raw: [$_]" } | Add-Content (Join-Path $PSScriptRoot "dev-android-diag.txt") -Encoding UTF8
        Invoke-Build
        @(& $adb devices 2>&1) | ForEach-Object { "post-build raw: [$_]" } | Add-Content (Join-Path $PSScriptRoot "dev-android-diag.txt") -Encoding UTF8
        $serial = Select-Device $adb
        Invoke-Install $adb $serial
    }
    "reinstall" {
        $serial = Select-Device $adb
        Invoke-Install $adb $serial
    }
    "log" {
        $serial = Select-Device $adb
        Write-Step "Streaming logs (tag: $LogTag, Ctrl+C to stop)"
        Write-Host "Key lines:" -ForegroundColor Yellow
        Write-Host "  service created / destroyed  -> foreground service lifecycle (destroyed = ROM killed it)"
        Write-Host "  cycle ok: collected=N ...   -> one collection+upload round (every ~60s)"
        Write-Host "  usage stats permission ...   -> usage access permission not granted yet"
        Write-Host "  cycle failed: ...           -> upload or collect error, retries next round"
        & $Adb -s $Serial logcat -v time -s $LogTag
    }
    "status" {
        $serial = Select-Device $adb
        Write-Step "Service process state on device"
        & $Adb -s $Serial shell "ps -A | grep activitytimeline || echo 'no process running'"
        Write-Step "Last 20 app log lines"
        & $Adb -s $Serial logcat -d -v time -s $LogTag -t 20
    }
}
