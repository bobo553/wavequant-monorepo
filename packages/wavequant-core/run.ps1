param(
    [string]$TdxRoot = 'D:\TDX',
    [string]$OutputDir = 'results\tdx'
)
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    $taskPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $taskPython)) {
        python -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ is required.' }
    }
    & $taskPython -c 'import pytdx, pandas'
    if ($LASTEXITCODE -ne 0) {
        & $taskPython -m pip install -e '.[tdx]'
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    }
    & $taskPython -m wavequant.interfaces.cli run-tdx --tdx-root $TdxRoot --output-dir $OutputDir
    if ($LASTEXITCODE -ne 0) { throw 'Pipeline failed. Review the error and test log.' }
    Write-Host "Report: $(Join-Path $OutputDir 'report.html')"
}
finally {
    Pop-Location
}
