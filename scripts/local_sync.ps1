param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet("backup", "check", "restore")]
    [string]$Action,

    [Parameter(Mandatory = $false)]
    [string]$SyncRoot,

    [switch]$AllowDeletions,
    [switch]$Force,
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$SyncRoot = if ([string]::IsNullOrWhiteSpace($SyncRoot)) { $env:EPUB_SYNC_ROOT } else { $SyncRoot }
if ([string]::IsNullOrWhiteSpace($SyncRoot)) {
    throw "Thiếu -SyncRoot hoặc biến môi trường EPUB_SYNC_ROOT."
}
$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$python = if (Test-Path -LiteralPath $venvPython) { $venvPython } else { "python" }
$script = Join-Path $PSScriptRoot "local_sync.py"

$arguments = @($script, $Action, "--sync-root", $SyncRoot, "--project-root", $projectRoot, "--port", $Port)
if ($AllowDeletions) { $arguments += "--allow-deletions" }
if ($Force) { $arguments += "--force" }

& $python @arguments
exit $LASTEXITCODE
