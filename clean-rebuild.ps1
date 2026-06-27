# ============================================================================
# 5G AIOps — Clean Rebuild Script for Windows
# ============================================================================
# What this does (in order):
#   1. Stops the current docker compose stack (if any)
#   2. Removes all old aiops5g containers, images, networks
#   3. Kills any zombie processes holding our ports (Python, Node, etc)
#   4. Restarts Docker Desktop to clear stuck port reservations
#   5. Verifies all required ports are free
#   6. Rebuilds and starts the stack
# ============================================================================

$ErrorActionPreference = "Continue"
$ProjectDir = "C:\Users\kenng\Documents\aiops5g\aiops5g"
$Ports = @(8001, 8002, 8003, 8004, 8005, 9000, 9001, 9002, 9003, 5173)

Write-Host "`n=========================================" -ForegroundColor Cyan
Write-Host "  5G AIOps — Clean Rebuild" -ForegroundColor Cyan
Write-Host "=========================================`n" -ForegroundColor Cyan

# --- Step 1: Navigate to project ---
Write-Host "[1/7] Navigating to project directory..." -ForegroundColor Yellow
if (-not (Test-Path $ProjectDir)) {
    Write-Host "ERROR: $ProjectDir does not exist." -ForegroundColor Red
    Write-Host "Edit this script and update `$ProjectDir to match your folder." -ForegroundColor Red
    exit 1
}
Set-Location $ProjectDir
Write-Host "  ✓ At $ProjectDir`n" -ForegroundColor Green

# --- Step 2: Stop docker compose stack ---
Write-Host "[2/7] Stopping any running compose stack..." -ForegroundColor Yellow
docker compose down --remove-orphans --volumes 2>&1 | Out-Null
Write-Host "  ✓ Compose stopped`n" -ForegroundColor Green

# --- Step 3: Remove old aiops5g containers and networks ---
Write-Host "[3/7] Cleaning old aiops5g containers, images, networks..." -ForegroundColor Yellow
$containers = docker ps -aq --filter "name=aiops5g" 2>$null
if ($containers) {
    docker rm -f $containers 2>&1 | Out-Null
    Write-Host "  ✓ Removed $($containers.Count) old containers" -ForegroundColor Green
}
docker network prune -f 2>&1 | Out-Null
docker container prune -f 2>&1 | Out-Null
Write-Host "  ✓ Networks pruned`n" -ForegroundColor Green

# --- Step 4: Kill non-Docker zombie processes on our ports ---
Write-Host "[4/7] Killing zombie processes (Python, Node) on aiops5g ports..." -ForegroundColor Yellow
$killedAny = $false
foreach ($port in $Ports) {
    $connections = netstat -ano | Select-String ":$port\s.*LISTENING"
    foreach ($line in $connections) {
        $parts = $line -split '\s+' | Where-Object { $_ -ne '' }
        $pid = $parts[-1]
        $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
        if (-not $proc) { continue }
        # Skip Docker processes — we'll restart Docker instead
        if ($proc.ProcessName -match 'docker|wslrelay|vpnkit|com\.docker') { continue }
        Write-Host "  Killing $($proc.ProcessName) (PID $pid) on port $port" -ForegroundColor Red
        Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
        $killedAny = $true
    }
}
if (-not $killedAny) { Write-Host "  ✓ No zombie processes found" -ForegroundColor Green }
Write-Host ""

# --- Step 5: Restart Docker Desktop to clear stuck port reservations ---
Write-Host "[5/7] Restarting Docker Desktop (releases stuck port reservations)..." -ForegroundColor Yellow
Get-Process "Docker Desktop" -ErrorAction SilentlyContinue | Stop-Process -Force
Get-Process "com.docker*"   -ErrorAction SilentlyContinue | Stop-Process -Force
Get-Process "wslrelay"      -ErrorAction SilentlyContinue | Stop-Process -Force
Get-Process "vpnkit*"       -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 5

# Find Docker Desktop executable
$dockerExe = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
if (-not (Test-Path $dockerExe)) {
    Write-Host "  WARNING: Docker Desktop not found at expected path." -ForegroundColor Yellow
    Write-Host "  Please open Docker Desktop manually and wait for green whale, then re-run this script." -ForegroundColor Yellow
    exit 1
}

Write-Host "  Starting Docker Desktop..." -ForegroundColor Cyan
Start-Process $dockerExe

Write-Host "  Waiting up to 120 seconds for Docker engine to be ready..." -ForegroundColor Cyan
$ready = $false
for ($i = 1; $i -le 24; $i++) {
    Start-Sleep -Seconds 5
    docker ps 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $ready = $true
        Write-Host "  ✓ Docker is ready (after $($i * 5)s)`n" -ForegroundColor Green
        break
    }
    Write-Host "    ... still waiting ($($i * 5)s)" -ForegroundColor Gray
}
if (-not $ready) {
    Write-Host "  ✗ Docker did not become ready within 2 minutes." -ForegroundColor Red
    Write-Host "  Open Docker Desktop manually, wait for green whale, then re-run." -ForegroundColor Red
    exit 1
}

# --- Step 6: Verify all ports are free ---
Write-Host "[6/7] Verifying all aiops5g ports are free..." -ForegroundColor Yellow
$blocked = @()
foreach ($port in $Ports) {
    $hits = netstat -ano | Select-String ":$port\s.*LISTENING"
    if ($hits) {
        $blocked += $port
        Write-Host "  ✗ Port $port still in use!" -ForegroundColor Red
        $hits | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkRed }
    }
}
if ($blocked.Count -gt 0) {
    Write-Host "`nABORT: Ports still blocked: $($blocked -join ', ')" -ForegroundColor Red
    Write-Host "Run 'tasklist /FI `"PID eq <PID>`"' on each PID above to identify, then close those programs." -ForegroundColor Yellow
    exit 1
}
Write-Host "  ✓ All ports free`n" -ForegroundColor Green

# --- Step 7: Build and start the stack ---
Write-Host "[7/7] Starting aiops5g stack (this terminal will keep it running)..." -ForegroundColor Yellow
Write-Host "  • Build takes ~3 minutes first time, ~30s on subsequent runs" -ForegroundColor Gray
Write-Host "  • Wait for all services to print 'Application startup complete'" -ForegroundColor Gray
Write-Host "  • Then open http://localhost:5173 in your browser" -ForegroundColor Gray
Write-Host "  • Press Ctrl+C in this window to stop everything`n" -ForegroundColor Gray

# If ANTHROPIC_API_KEY is set in the user's env, pass it through
if ($env:ANTHROPIC_API_KEY) {
    Write-Host "  ✓ ANTHROPIC_API_KEY detected — LLM Agent will work`n" -ForegroundColor Green
} else {
    Write-Host "  ⚠ ANTHROPIC_API_KEY not set — LLM Agent tab will say 'not configured'" -ForegroundColor Yellow
    Write-Host "    To enable: close this terminal, run 'set ANTHROPIC_API_KEY=sk-ant-...', then re-run this script`n" -ForegroundColor Yellow
}

docker compose up --build
