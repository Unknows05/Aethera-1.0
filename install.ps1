# Aethera v1.5 — Windows Installer
# Usage: irm https://raw.githubusercontent.com/Unknows05/Aethera/main/install.ps1 | iex

$ErrorActionPreference = "Stop"
$repo = "https://github.com/Unknows05/Aethera-1.0.git"
$installDir = if ($env:AETHERA_DIR) { $env:AETHERA_DIR } else { "$env:USERPROFILE\aethera" }
$version = "1.5.0"

Write-Host ""
Write-Host "  ╔══════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "  ║   Aethera v$version — Installer        ║" -ForegroundColor Cyan
Write-Host "  ║   Autonomous AI Trading Agent        ║" -ForegroundColor Cyan
Write-Host "  ╚══════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# ── Checks ──────────────────────────────────────────────

function Check-Command {
    param($Name)
    if (Get-Command $Name -ErrorAction SilentlyContinue) {
        Write-Host "[+] $Name found" -ForegroundColor Green
        return $true
    } else {
        Write-Host "[-] $Name not found. Please install it first." -ForegroundColor Red
        return $false
    }
}

Write-Host "Checking dependencies..."
if (-not (Check-Command "git")) { exit 1 }
if (-not (Check-Command "python")) { exit 1 }

$pythonVersion = python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
Write-Host "[+] Python $pythonVersion" -ForegroundColor Green

$hasNode = $false
if (Check-Command "node") {
    $nodeVersion = node -v
    Write-Host "[+] Node.js $nodeVersion (for TUI)" -ForegroundColor Green
    $hasNode = $true
} else {
    Write-Host "[!] Node.js not found — TUI will be built when needed" -ForegroundColor Yellow
}

# ── Install ─────────────────────────────────────────────

if (Test-Path "$installDir\.git") {
    Write-Host "`nAethera already installed at $installDir" -ForegroundColor Yellow
    Write-Host "Updating instead of fresh install..." -ForegroundColor Yellow
    Set-Location $installDir
    git fetch origin main
    git reset --hard origin/main
    Write-Host "[+] Updated to latest" -ForegroundColor Green
} else {
    Write-Host "`nInstalling Aethera to $installDir..." -ForegroundColor Cyan
    git clone $repo $installDir
    Set-Location $installDir
    Write-Host "[+] Cloned repository" -ForegroundColor Green
}

# ── Python Dependencies ─────────────────────────────────

Write-Host "`nInstalling Python dependencies..." -ForegroundColor Cyan
python -m pip install --quiet --upgrade pip
python -m pip install --quiet click rich requests openai ccxt pyyaml httpx apscheduler pynacl
Write-Host "[+] Python dependencies installed" -ForegroundColor Green

# ── Build TUI ───────────────────────────────────────────

if ($hasNode -and (Test-Path "tui")) {
    Write-Host "`nBuilding TypeScript TUI..." -ForegroundColor Cyan
    Set-Location tui
    npm install --silent 2>$null
    npm run build 2>$null
    Set-Location ..
    Write-Host "[+] TUI built" -ForegroundColor Green
} else {
    Write-Host "`n[!] Skipping TUI build (Node.js not available)" -ForegroundColor Yellow
}

# ── Create data directory ───────────────────────────────

New-Item -ItemType Directory -Force -Path "$installDir\data" | Out-Null
New-Item -ItemType Directory -Force -Path "$installDir\vault" | Out-Null

# ── Done ────────────────────────────────────────────────

Write-Host ""
Write-Host "  ╔══════════════════════════════════════╗" -ForegroundColor Green
Write-Host "  ║   Aethera v$version installed!           ║" -ForegroundColor Green
Write-Host "  ╚══════════════════════════════════════╝" -ForegroundColor Green
Write-Host ""
Write-Host "  Next steps:" -ForegroundColor Cyan
Write-Host "    1. aethera init       — Setup wizard" -ForegroundColor Yellow
Write-Host "    2. aethera start      — Launch TUI" -ForegroundColor Yellow
Write-Host "    3. aethera --help     — All commands" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Update:" -ForegroundColor Cyan
Write-Host "    aethera update       — Auto-update to latest" -ForegroundColor Yellow
Write-Host ""

# Ask to run init
$answer = Read-Host "Run 'aethera init' now? [Y/n]"
if ($answer -ne "n" -and $answer -ne "N") {
    Set-Location $installDir
    python cli.py init
}
