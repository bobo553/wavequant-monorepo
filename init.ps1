param(
    [ValidateSet("quick", "full")]
    [string]$Mode = "quick"
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

node --version
pnpm --version

if (-not (Test-Path -LiteralPath "node_modules")) {
    pnpm install --frozen-lockfile
}

if ($Mode -eq "full") {
    pnpm verify
} else {
    pnpm verify:quick
}
