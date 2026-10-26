param(
    [int]$Port = 8765,
    [string]$PublicOrigin = ''
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$engine = Join-Path $projectRoot 'target\release\sao-denoise.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Create .venv in the Hush product directory and install test-app/requirements.txt first.'
}
if (-not (Test-Path -LiteralPath $engine)) {
    throw 'Build the Rust engine first with cargo build --release --locked.'
}
$serverArguments = @((Join-Path $PSScriptRoot 'server.py'), '--port', $Port, '--executable', $engine)
if ($PublicOrigin) {
    $serverArguments += @('--public-origin', $PublicOrigin)
}
& $python @serverArguments
