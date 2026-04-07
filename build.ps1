#!/usr/bin/env pwsh
<#
.SYNOPSIS
    VocalTwist build script — minify frontend JS/CSS and compile Python to bytecode.

.DESCRIPTION
    Produces a distributable `dist/` directory containing:
      dist/frontend/   — minified JS + CSS (no raw source)
      dist/backend/    — Python .pyc bytecode (no .py source)
      dist/VocalTwistTest/ — compiled demo app

    Run this after every change before shipping to consumers who should not
    receive raw source.  See SHIPPING.md for integration instructions.

.PARAMETER Clean
    Delete dist/ before building (default: true).

.PARAMETER SkipFrontend
    Skip JavaScript/CSS minification step.

.PARAMETER SkipBackend
    Skip Python bytecode compilation step.

.EXAMPLE
    .\build.ps1
    .\build.ps1 -SkipFrontend
    .\build.ps1 -Clean:$false
#>
param(
    [switch]$Clean        = $true,
    [switch]$SkipFrontend = $false,
    [switch]$SkipBackend  = $false
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ROOT   = $PSScriptRoot
$DIST   = Join-Path $ROOT "dist"
$VENV   = Join-Path $ROOT ".venv"
$PYTHON = Join-Path $VENV "Scripts\python.exe"

function Info  { param($msg) Write-Host "  [build] $msg" -ForegroundColor Cyan }
function Ok    { param($msg) Write-Host "  [build] $msg" -ForegroundColor Green }
function Warn  { param($msg) Write-Host "  [build] $msg" -ForegroundColor Yellow }
function Die   { param($msg) Write-Host "  [build] ERROR: $msg" -ForegroundColor Red; exit 1 }

# ── Preflight ──────────────────────────────────────────────────────────────────
if (-not (Test-Path $PYTHON)) { Die "Virtual environment not found at $VENV — run: python -m venv .venv && pip install -r requirements.txt" }
if (-not $SkipFrontend) {
    if (-not (Get-Command node -ErrorAction SilentlyContinue)) { Die "Node.js not found. Install from https://nodejs.org or pass -SkipFrontend to skip JS minification." }
}

# ── Clean ──────────────────────────────────────────────────────────────────────
if ($Clean -and (Test-Path $DIST)) {
    Info "Removing previous dist/"
    Remove-Item $DIST -Recurse -Force
}
New-Item -ItemType Directory -Force $DIST | Out-Null

# ── 1. Frontend — minify JS and CSS ───────────────────────────────────────────
if (-not $SkipFrontend) {
    Info "Minifying frontend JS and CSS with esbuild..."

    $FDIST = Join-Path $DIST "frontend"
    New-Item -ItemType Directory -Force $FDIST | Out-Null

    # Install esbuild locally if not present (zero global installs required)
    $esbuild = Join-Path $ROOT "node_modules\.bin\esbuild.cmd"
    if (-not (Test-Path $esbuild)) {
        Info "Installing esbuild (dev-only, local)..."
        Push-Location $ROOT
        npm install --save-dev esbuild --silent 2>&1 | Out-Null
        Pop-Location
    }

    # Minify each JS file
    foreach ($jsFile in @("vocal-twist.js", "ambient-vad.js")) {
        $src  = Join-Path $ROOT "frontend\$jsFile"
        $base = [System.IO.Path]::GetFileNameWithoutExtension($jsFile)
        $out  = Join-Path $FDIST "$base.min.js"
        & $esbuild $src --bundle=false --minify --outfile=$out
        $srcKB = [math]::Round((Get-Item $src).Length / 1KB, 1)
        $outKB = [math]::Round((Get-Item $out).Length / 1KB, 1)
        Ok "$jsFile  ${srcKB}KB → ${outKB}KB"
    }

    # Minify CSS
    $cssSrc = Join-Path $ROOT "frontend\vocal-twist.css"
    $cssOut = Join-Path $FDIST "vocal-twist.min.css"
    & $esbuild $cssSrc --minify --outfile=$cssOut
    $cKB = [math]::Round((Get-Item $cssSrc).Length / 1KB, 1)
    $oKB = [math]::Round((Get-Item $cssOut).Length / 1KB, 1)
    Ok "vocal-twist.css  ${cKB}KB → ${oKB}KB"

    # Copy sample-usage.html and update script paths to point to .min files
    $htmlSrc = Join-Path $ROOT "frontend\sample-usage.html"
    $htmlDst = Join-Path $FDIST "sample-usage.html"
    (Get-Content $htmlSrc -Raw) `
        -replace 'vocal-twist\.js',  'vocal-twist.min.js' `
        -replace 'ambient-vad\.js',  'ambient-vad.min.js' `
        -replace 'vocal-twist\.css', 'vocal-twist.min.css' |
        Set-Content $htmlDst -Encoding UTF8
    Ok "sample-usage.html updated"

} else {
    Warn "Skipping frontend minification (-SkipFrontend)"
}

# ── 2. Backend — compile Python source to bytecode ────────────────────────────
if (-not $SkipBackend) {
    Info "Compiling Python sources to bytecode..."

    $BDIST = Join-Path $DIST "backend"
    $DDIST = Join-Path $DIST "VocalTwistTest"

    # compileall: -b puts .pyc alongside .py; we then strip .py in dist copy
    $compileScript = @"
import compileall, shutil, os, pathlib

root = pathlib.Path(r'$ROOT')
dist = pathlib.Path(r'$DIST')

for pkg in ('backend', 'VocalTwistTest'):
    src_dir = root / pkg
    dst_dir = dist / pkg
    dst_dir.mkdir(parents=True, exist_ok=True)

    # Compile all .py files (writes __pycache__/*.pyc)
    compileall.compile_dir(str(src_dir), force=True, quiet=True, legacy=False)

    # Copy everything EXCEPT .py source files
    for src in src_dir.rglob('*'):
        rel = src.relative_to(src_dir)
        dst = dst_dir / rel
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
        elif src.suffix == '.py':
            pass  # skip raw source
        elif src.suffix == '.pyc' or src.name.startswith('__pycache__'):
            pass  # handled separately below — copy pyc files up one level
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    # Promote __pycache__/*.pyc → alongside original (legacy layout for importability)
    for pyc in src_dir.rglob('__pycache__/*.pyc'):
        # e.g. backend/__pycache__/config.cpython-313.pyc -> dist/backend/config.pyc
        module_stem = pyc.stem.split('.')[0]   # strip .cpython-313
        rel_parent  = pyc.parent.parent.relative_to(src_dir)
        dst = dist / pkg / rel_parent / (module_stem + '.pyc')
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pyc, dst)

print('Bytecode compilation complete')
"@
    & $PYTHON -c $compileScript
    Ok "backend/ and VocalTwistTest/ compiled to .pyc"

    # Copy non-Python assets that the app needs at runtime
    $assets = @(
        @{ From="VocalTwistTest\static";  To="$DIST\VocalTwistTest\static" },
        @{ From=".env.example";           To="$DIST\.env.example" },
        @{ From="requirements.txt";       To="$DIST\requirements.txt" },
        @{ From="openapi.yaml";           To="$DIST\openapi.yaml" },
        @{ From="README.md";              To="$DIST\README.md" },
        @{ From="INTEGRATION.md";         To="$DIST\INTEGRATION.md" },
        @{ From="SHIPPING.md";            To="$DIST\SHIPPING.md" },
        @{ From="LICENSE";                To="$DIST\LICENSE" },
        @{ From="start.ps1";              To="$DIST\start.ps1" }
    )
    foreach ($a in $assets) {
        $src = Join-Path $ROOT $a.From
        if (Test-Path $src) {
            $dst = $a.To
            if ((Get-Item $src).PSIsContainer) {
                Copy-Item $src $dst -Recurse -Force
            } else {
                $dstDir = Split-Path $dst -Parent
                New-Item -ItemType Directory -Force $dstDir | Out-Null
                Copy-Item $src $dst -Force
            }
            Ok "Copied $($a.From)"
        }
    }

    # If frontend dist was also built, copy minified JS into VocalTwistTest static
    if (-not $SkipFrontend) {
        $vtStatic = Join-Path $DIST "VocalTwistTest\static"
        $fDistDir = Join-Path $DIST "frontend"
        # Copy minified files so the demo app can serve them
        foreach ($f in Get-ChildItem $fDistDir -File) {
            Copy-Item $f.FullName (Join-Path $vtStatic $f.Name) -Force
        }
        # Also copy minified JS to the dist/frontend folder served by the app routes
        Copy-Item $fDistDir (Join-Path $DIST "frontend") -Recurse -Force -ErrorAction SilentlyContinue
        Ok "Minified frontend assets copied into VocalTwistTest/static"
    }

} else {
    Warn "Skipping backend compilation (-SkipBackend)"
}

# ── 3. Write a launch wrapper ──────────────────────────────────────────────────
$launchScript = @'
#!/usr/bin/env pwsh
# Launch VocalTwist from the compiled dist/ directory
$DIST = $PSScriptRoot
Set-Location $DIST
if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..."
    python -m venv .venv
    .\.venv\Scripts\pip.exe install -r requirements.txt --quiet
}
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env" }
$env:PYTHONPATH = $DIST
.\.venv\Scripts\uvicorn.exe VocalTwistTest.app:app --port 8000
'@
$launchScript | Set-Content (Join-Path $DIST "start.ps1") -Encoding UTF8

# ── Summary ───────────────────────────────────────────────────────────────────
Write-Host ""
Ok "Build complete → $DIST"
Write-Host ""
Write-Host "  To run the compiled build:" -ForegroundColor White
Write-Host "    cd dist && .\start.ps1" -ForegroundColor Gray
Write-Host ""
Write-Host "  See SHIPPING.md for integration instructions." -ForegroundColor White
