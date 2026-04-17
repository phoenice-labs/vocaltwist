#!/usr/bin/env pwsh
<#
.SYNOPSIS
    VocalTwist Docker Management Script

.DESCRIPTION
    Convenience wrapper for common docker compose operations.
    All commands assume docker-compose.yml is in the same directory.

.EXAMPLE
    .\docker-manage.ps1 start       # Build (if needed) and start in background
    .\docker-manage.ps1 stop        # Stop and remove containers
    .\docker-manage.ps1 restart     # Stop then start
    .\docker-manage.ps1 build       # Force-rebuild the image
    .\docker-manage.ps1 logs        # Follow live container logs
    .\docker-manage.ps1 status      # Show container health
    .\docker-manage.ps1 shell       # Open a bash shell inside the container
    .\docker-manage.ps1 test        # Run the e2e test suite against a running backend
    .\docker-manage.ps1 test-offline # Run offline-fallback tests only
    .\docker-manage.ps1 help        # Print this help text
#>

param(
    [Parameter(Position = 0)]
    [ValidateSet("start","stop","restart","build","logs","status","shell","test","test-offline","help")]
    [string]$Command = "help"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ─── Paths ────────────────────────────────────────────────────────────────────

$ScriptDir   = $PSScriptRoot
$ComposeFile = Join-Path $ScriptDir "docker-compose.yml"
$BackendUrl  = "http://localhost:8000"

# ─── Helpers ──────────────────────────────────────────────────────────────────

function Write-Header([string]$text) {
    Write-Host ""
    Write-Host "━━━ $text ━━━" -ForegroundColor Cyan
}

function Assert-DockerRunning {
    try {
        docker info --format '{{.ServerVersion}}' 2>$null | Out-Null
    } catch {
        Write-Host "❌  Docker is not running. Please start Docker Desktop first." -ForegroundColor Red
        exit 1
    }
}

function Wait-BackendHealthy([int]$MaxSeconds = 60) {
    Write-Host "⏳  Waiting for backend to become healthy (max ${MaxSeconds}s)..." -ForegroundColor Yellow
    $deadline = [DateTime]::Now.AddSeconds($MaxSeconds)
    while ([DateTime]::Now -lt $deadline) {
        try {
            $resp = Invoke-WebRequest -Uri "$BackendUrl/api/health" -UseBasicParsing -TimeoutSec 3
            if ($resp.StatusCode -eq 200) {
                Write-Host "✅  Backend is healthy at $BackendUrl" -ForegroundColor Green
                return
            }
        } catch {
            # still starting up
        }
        Start-Sleep -Seconds 2
    }
    Write-Host "⚠️   Backend did not become healthy within ${MaxSeconds}s." -ForegroundColor Yellow
}

# ─── Commands ─────────────────────────────────────────────────────────────────

function Invoke-Start {
    Write-Header "Starting VocalTwist"
    Assert-DockerRunning
    docker compose -f $ComposeFile up -d --remove-orphans
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Wait-BackendHealthy
    Write-Host ""
    Write-Host "🌐  Backend:    $BackendUrl"
    Write-Host "🩺  Health:     $BackendUrl/api/health"
    Write-Host "📄  Test page:  $BackendUrl/test-extension.html"
    Write-Host ""
    Write-Host "Run '.\docker-manage.ps1 logs'   to follow live logs."
    Write-Host "Run '.\docker-manage.ps1 stop'   to shut down."
}

function Invoke-Stop {
    Write-Header "Stopping VocalTwist"
    Assert-DockerRunning
    docker compose -f $ComposeFile down
}

function Invoke-Restart {
    Invoke-Stop
    Invoke-Start
}

function Invoke-Build {
    Write-Header "Building VocalTwist image (no cache)"
    Assert-DockerRunning
    docker compose -f $ComposeFile build --no-cache
    Write-Host ""
    Write-Host "✅  Build complete. Run '.\docker-manage.ps1 start' to launch." -ForegroundColor Green
}

function Invoke-Logs {
    Write-Header "Tailing VocalTwist logs (Ctrl+C to stop)"
    Assert-DockerRunning
    docker compose -f $ComposeFile logs -f
}

function Invoke-Status {
    Write-Header "VocalTwist Container Status"
    Assert-DockerRunning
    docker compose -f $ComposeFile ps
    Write-Host ""
    try {
        $resp = Invoke-WebRequest -Uri "$BackendUrl/api/health" -UseBasicParsing -TimeoutSec 5
        $health = $resp.Content | ConvertFrom-Json
        Write-Host "🟢  Backend responding: $($health | ConvertTo-Json -Compress)" -ForegroundColor Green
    } catch {
        Write-Host "🔴  Backend not responding at $BackendUrl" -ForegroundColor Red
    }
}

function Invoke-Shell {
    Write-Header "Opening shell in VocalTwist container"
    Assert-DockerRunning
    $running = docker compose -f $ComposeFile ps --format json 2>$null |
        ConvertFrom-Json |
        Where-Object { $_.Service -eq "vocaltwist" -and $_.State -eq "running" }
    if (-not $running) {
        Write-Host "⚠️   Container is not running. Start it first with: .\docker-manage.ps1 start" -ForegroundColor Yellow
        exit 1
    }
    docker compose -f $ComposeFile exec vocaltwist bash
}

function Invoke-Test([string]$TestPath = "") {
    Write-Header "Running VocalTwist E2E Tests"
    # Verify backend is up before running tests
    try {
        Invoke-WebRequest -Uri "$BackendUrl/api/health" -UseBasicParsing -TimeoutSec 5 | Out-Null
    } catch {
        Write-Host "❌  Backend not reachable at $BackendUrl. Start it first." -ForegroundColor Red
        exit 1
    }

    $testDir  = Join-Path $ScriptDir "VocalTwistTest"
    $specDir  = Join-Path $testDir "tests" "e2e"
    $testFile = if ($TestPath) { $TestPath } else { $specDir }

    # Activate virtualenv if present
    $venv = Join-Path $ScriptDir ".venv"
    if (Test-Path (Join-Path $venv "Scripts" "Activate.ps1")) {
        & (Join-Path $venv "Scripts" "Activate.ps1")
    }

    Write-Host "📋  Running: pytest $testFile -v" -ForegroundColor Cyan
    python -m pytest $testFile -v
}

function Invoke-TestOffline {
    $offlineFile = Join-Path $ScriptDir "VocalTwistTest" "tests" "e2e" "test_extension_offline_e2e.py"
    Invoke-Test -TestPath $offlineFile
}

function Invoke-Help {
    Write-Host @"

VocalTwist Docker Management
─────────────────────────────────────────────────────────
  .\docker-manage.ps1 start         Start backend in background
  .\docker-manage.ps1 stop          Stop and remove containers
  .\docker-manage.ps1 restart       Stop then start
  .\docker-manage.ps1 build         Force-rebuild the Docker image
  .\docker-manage.ps1 logs          Follow live container logs
  .\docker-manage.ps1 status        Show container and backend health
  .\docker-manage.ps1 shell         Open bash shell inside container
  .\docker-manage.ps1 test          Run all e2e tests (backend + offline)
  .\docker-manage.ps1 test-offline  Run offline-fallback tests only
  .\docker-manage.ps1 help          Print this help

Prerequisites
  - Docker Desktop running
  - Python environment with playwright + pytest installed

Ports
  - Backend:  http://localhost:8000
  - Test page: http://localhost:8000/test-extension.html
"@
}

# ─── Dispatch ─────────────────────────────────────────────────────────────────

switch ($Command) {
    "start"        { Invoke-Start }
    "stop"         { Invoke-Stop }
    "restart"      { Invoke-Restart }
    "build"        { Invoke-Build }
    "logs"         { Invoke-Logs }
    "status"       { Invoke-Status }
    "shell"        { Invoke-Shell }
    "test"         { Invoke-Test }
    "test-offline" { Invoke-TestOffline }
    default        { Invoke-Help }
}
