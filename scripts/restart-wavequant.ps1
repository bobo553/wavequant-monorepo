[CmdletBinding()]
param(
    [int]$PreferredApiPort = 8765,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$runtimeDirectory = Join-Path $repositoryRoot ".codex-runtime"
$pidFile = Join-Path $runtimeDirectory "wavequant-dev.pid"
$webPort = 3003
$webUrl = "http://127.0.0.1:$webPort"

function Stop-OwnedProcessTree {
    param([int]$ProcessId)

    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
    if (-not $process) {
        return
    }

    Write-Host "Stopping WaveQuant process tree (PID $ProcessId)..."
    $result = Start-Process -FilePath "taskkill.exe" -ArgumentList "/PID", $ProcessId, "/T", "/F" -WindowStyle Hidden -Wait -PassThru
    if ($result.ExitCode -notin 0, 128) {
        throw "Unable to stop WaveQuant process tree (taskkill exit code $($result.ExitCode))."
    }
}

function Find-DevRootProcess {
    $connection = Get-NetTCPConnection -State Listen -LocalPort $webPort -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $connection) {
        return $null
    }

    $processes = Get-CimInstance Win32_Process
    $byId = @{}
    foreach ($process in $processes) {
        $byId[[int]$process.ProcessId] = $process
    }

    $currentId = [int]$connection.OwningProcess
    $candidate = $null
    while ($currentId -and $byId.ContainsKey($currentId)) {
        $process = $byId[$currentId]
        $commandLine = [string]$process.CommandLine
        if ($commandLine -match "wavequant-web" -and $commandLine -match "\bdev\b") {
            $candidate = [int]$process.ProcessId
        }
        $currentId = [int]$process.ParentProcessId
    }
    return $candidate
}

function Test-DevRootProcess {
    param([int]$ProcessId)

    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
    if (-not $process) {
        return $false
    }
    $commandLine = [string]$process.CommandLine
    return $commandLine -match "wavequant-web" -and $commandLine -match "\bdev\b"
}

function Test-PortAvailable {
    param([int]$Port)

    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
    try {
        $listener.Start()
        return $true
    }
    catch {
        return $false
    }
    finally {
        $listener.Stop()
    }
}

function Select-ApiPort {
    param([int]$PreferredPort)

    $candidates = @($PreferredPort) + (9200..9299)
    foreach ($candidate in $candidates | Select-Object -Unique) {
        if ($candidate -ne $webPort -and (Test-PortAvailable -Port $candidate)) {
            return $candidate
        }
    }
    throw "No available API port was found (checked $PreferredPort and 9200-9299)."
}

function Wait-ForHttpOk {
    param(
        [string]$Url,
        [int]$TimeoutSeconds = 90
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 10
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) {
                return
            }
        }
        catch {
            Start-Sleep -Seconds 1
        }
    } while ((Get-Date) -lt $deadline)

    throw "Timed out waiting for $Url."
}

New-Item -ItemType Directory -Force -Path $runtimeDirectory | Out-Null

$savedProcessId = 0
if (Test-Path -LiteralPath $pidFile) {
    [void][int]::TryParse((Get-Content -LiteralPath $pidFile -Raw).Trim(), [ref]$savedProcessId)
}
$detectedProcessId = Find-DevRootProcess
$processIdToStop = if ($savedProcessId -and (Test-DevRootProcess -ProcessId $savedProcessId)) {
    $savedProcessId
}
else {
    $detectedProcessId
}
if ($processIdToStop) {
    Stop-OwnedProcessTree -ProcessId $processIdToStop
}

$portDeadline = (Get-Date).AddSeconds(15)
while ((Get-NetTCPConnection -State Listen -LocalPort $webPort -ErrorAction SilentlyContinue) -and (Get-Date) -lt $portDeadline) {
    Start-Sleep -Milliseconds 250
}
if (Get-NetTCPConnection -State Listen -LocalPort $webPort -ErrorAction SilentlyContinue) {
    throw "Port $webPort is still occupied. Refusing to stop an unrelated process."
}

$apiPort = Select-ApiPort -PreferredPort $PreferredApiPort
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$stdoutLog = Join-Path $runtimeDirectory "wavequant-dev-$timestamp.stdout.log"
$stderrLog = Join-Path $runtimeDirectory "wavequant-dev-$timestamp.stderr.log"

$env:WAVEQUANT_API_PORT = [string]$apiPort
$developmentProcess = Start-Process `
    -FilePath "pnpm.cmd" `
    -ArgumentList "--filter", "wavequant-web", "dev" `
    -WorkingDirectory $repositoryRoot `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -WindowStyle Hidden `
    -PassThru
Set-Content -LiteralPath $pidFile -Value $developmentProcess.Id -Encoding ascii

Write-Host "Starting WaveQuant (PID $($developmentProcess.Id), API port $apiPort)..."
try {
    Wait-ForHttpOk -Url $webUrl
    Wait-ForHttpOk -Url "$webUrl/api/catalog"
}
catch {
    Write-Host "Startup failed. Recent errors:" -ForegroundColor Red
    Get-Content -LiteralPath $stderrLog -Tail 30 -ErrorAction SilentlyContinue
    throw
}

Write-Host "WaveQuant is ready: $webUrl" -ForegroundColor Green
Write-Host "API port: $apiPort"
Write-Host "Logs: $stdoutLog"
if (-not $NoBrowser) {
    Start-Process $webUrl
}
