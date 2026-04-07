#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Start or stop the VocalTwistTest development server.

.DESCRIPTION
    Manages the uvicorn server for VocalTwistTest (port 8000 by default).
    Cleans up any hung processes on the target port before starting.

.PARAMETER Action
    'start'  — Kill any process on $Port, then launch uvicorn (default).
    'stop'   — Kill any process on $Port and exit.
    'status' — Show whether the server is currently running on $Port.

.PARAMETER Port
    TCP port to use. Default: 8000.

.PARAMETER Reload
    Pass --reload to uvicorn so the server auto-restarts on code changes.
    Default: $true in development.

.EXAMPLE
    .\start.ps1                     # start with defaults
    .\start.ps1 -Action stop        # stop the server
    .\start.ps1 -Port 8001          # start on port 8001
    .\start.ps1 -Reload:$false      # start without hot-reload
#>
param(
    [ValidateSet("start", "stop", "status")]
    [string]$Action = "start",

    [int]$Port = 8000,

    [bool]$Reload = $true
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Resolve paths ──────────────────────────────────────────────────────────────
$ScriptDir  = $PSScriptRoot
$VenvPython = Join-Path $ScriptDir ".venv\Scripts\python.exe"
$VenvPip    = Join-Path $ScriptDir ".venv\Scripts\pip.exe"
$EnvFile    = Join-Path $ScriptDir ".env"

function Write-Header([string]$msg) {
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}

function Write-Ok([string]$msg)   { Write-Host "  [OK]  $msg" -ForegroundColor Green }
function Write-Warn([string]$msg) { Write-Host "  [!!]  $msg" -ForegroundColor Yellow }
function Write-Err([string]$msg)  { Write-Host "  [XX]  $msg" -ForegroundColor Red }

# ── Find and kill processes holding $Port ─────────────────────────────────────
function Stop-PortProcess([int]$port) {
    $connections = netstat -ano 2>$null |
        Select-String -Pattern "TCP\s+.*:$port\s+.*LISTENING" |
        ForEach-Object { ($_ -split '\s+')[-1] } |
        Sort-Object -Unique

    if (-not $connections) {
        Write-Ok "No process found listening on port $port."
        return
    }

    foreach ($pid in $connections) {
        if ($pid -match '^\d+$') {
            try {
                $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
                if ($proc) {
                    Write-Warn "Killing PID $pid ($($proc.ProcessName)) on port $port ..."
                    Stop-Process -Id $pid -Force
                    Write-Ok "Killed PID $pid."
                }
            } catch {
                Write-Warn "Could not kill PID ${pid}: $_"
            }
        }
    }
}

# ── Check if something is listening on $Port ──────────────────────────────────
function Test-PortInUse([int]$port) {
    $result = netstat -ano 2>$null |
        Select-String -Pattern "TCP\s+.*:$port\s+.*LISTENING"
    return [bool]$result
}

# ── Validate prerequisites ────────────────────────────────────────────────────
function Assert-Prerequisites {
    if (-not (Test-Path $VenvPython)) {
        Write-Err "Virtual environment not found at: $VenvPython"
        Write-Err "Create it first:  python -m venv .venv"
        Write-Err "Then install deps: .\.venv\Scripts\pip install -r requirements.txt"
        exit 1
    }

    if (-not (Test-Path $EnvFile)) {
        Write-Warn ".env file not found — copying from .env.example ..."
        $example = Join-Path $ScriptDir ".env.example"
        if (Test-Path $example) {
            Copy-Item $example $EnvFile
            Write-Ok ".env created from .env.example"
        } else {
            Write-Err ".env.example also missing. Cannot continue."
            exit 1
        }
    }
}

# ── ACTION: status ────────────────────────────────────────────────────────────
if ($Action -eq "status") {
    Write-Header "Server status — port $Port"
    if (Test-PortInUse $Port) {
        Write-Ok "VocalTwistTest appears to be running on port $Port."
        Write-Host "  Open: http://localhost:$Port" -ForegroundColor White
    } else {
        Write-Warn "Nothing is listening on port $Port."
    }
    exit 0
}

# ── ACTION: stop ─────────────────────────────────────────────────────────────
if ($Action -eq "stop") {
    Write-Header "Stopping server on port $Port ..."
    Stop-PortProcess $Port
    Write-Ok "Done."
    exit 0
}

# ── ACTION: start ─────────────────────────────────────────────────────────────
Write-Header "VocalTwistTest — Starting server"

Assert-Prerequisites

# Clean up any hung process on the target port
if (Test-PortInUse $Port) {
    Write-Warn "Port $Port is already in use — cleaning up ..."
    Stop-PortProcess $Port
    Start-Sleep -Seconds 1
}

# Build uvicorn command
$uvicornArgs = @(
    "-m", "uvicorn",
    "VocalTwistTest.app:app",
    "--port", "$Port",
    "--host", "0.0.0.0"
)
if ($Reload) {
    $uvicornArgs += "--reload"
}

Write-Ok "Python  : $VenvPython"
Write-Ok "Port    : $Port"
Write-Ok "Reload  : $Reload"
Write-Ok "Env file: $EnvFile"
Write-Host ""
Write-Host "  Server URL: http://localhost:$Port" -ForegroundColor White
Write-Host "  API docs  : http://localhost:$Port/docs" -ForegroundColor White
Write-Host "  Press Ctrl+C to stop." -ForegroundColor DarkGray
Write-Host ""

# Change to project root so relative imports resolve correctly
Set-Location $ScriptDir

& $VenvPython @uvicornArgs
