param(
    [string]$Repository = "https://github.com/ActivityWatch/activitywatch.git",
    [string]$CloneDir = "D:\tmp\activitywatch-clone",
    [string]$Proxy = "",
    [switch]$Run
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message"
}

function Need-Command {
    param(
        [string]$Name,
        [string]$InstallHint
    )

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Missing command '$Name'. $InstallHint"
    }
}

function Configure-ProcessProxy {
    if ($Proxy) {
        $env:HTTP_PROXY = $Proxy
        $env:HTTPS_PROXY = $Proxy
        $env:ALL_PROXY = $Proxy
        Write-Host "Using process proxy: $Proxy"
        return
    }

    foreach ($name in "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY") {
        $value = [Environment]::GetEnvironmentVariable($name, "Process")
        if ($value -eq "http://127.0.0.1:9") {
            Remove-Item "Env:$name" -ErrorAction SilentlyContinue
            Write-Host "Removed invalid process proxy $name=$value"
        }
    }
}

function Assert-SafeCloneDir {
    $full = [System.IO.Path]::GetFullPath($CloneDir)
    $tmp = [System.IO.Path]::GetFullPath("D:\tmp")
    if (-not $full.StartsWith($tmp, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "CloneDir must stay inside D:\tmp. Got: $full"
    }
}

Set-Location $ProjectRoot
Configure-ProcessProxy

Write-Step "Checking local prerequisites"
Need-Command git "Install Git for Windows first."
Need-Command python "Install Python 3.10+ first."
Need-Command node "Install Node.js 20+ first."
Need-Command npm "Install npm with Node.js first."

$nodeMajor = [int]((& node --version).TrimStart("v").Split(".")[0])
if ($nodeMajor -lt 20) {
    throw "Node.js 20+ is required for current ActivityWatch frontend tooling. Current: $(node --version)"
}

Write-Step "Fetching ActivityWatch source"
if (-not (Test-Path -LiteralPath ".git")) {
    Assert-SafeCloneDir
    if (Test-Path -LiteralPath $CloneDir) {
        Remove-Item -LiteralPath $CloneDir -Recurse -Force
    }

    git clone --recursive $Repository $CloneDir

    Get-ChildItem -LiteralPath $CloneDir -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $ProjectRoot -Recurse -Force
    }
} else {
    git -C $ProjectRoot fetch --all --tags
    git -C $ProjectRoot submodule update --init --recursive
}

Write-Step "Preparing Python build tooling"
if (-not (Test-Path -LiteralPath ".venv-tools")) {
    python -m venv .venv-tools
}

$poetry = Join-Path $ProjectRoot ".venv-tools\Scripts\poetry.exe"
$pip = Join-Path $ProjectRoot ".venv-tools\Scripts\python.exe"
& $pip -m pip install --upgrade pip poetry
$env:PATH = "$(Split-Path $poetry);$env:PATH"

Write-Step "Building ActivityWatch"
Need-Command make "Install GNU Make, then rerun this script. On Windows, Git Bash/MSYS2/Chocolatey/Scoop are common options."
make build

if ($Run) {
    Write-Step "Starting ActivityWatch"
    make run
}

Write-Host ""
Write-Host "ActivityWatch deployment finished in $ProjectRoot"
