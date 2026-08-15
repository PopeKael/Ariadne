param(
    [switch]$OpenBrowser
)

$ErrorActionPreference = 'Stop'

$controlPlane = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $controlPlane
$tray = Join-Path $controlPlane 'tray.py'
$url = 'http://127.0.0.1:8765/'

if (-not (Test-Path -LiteralPath $tray -PathType Leaf)) {
    throw "Ariadne tray entry point not found: $tray"
}

# Prefer an explicitly configured interpreter, then the bundled runtime that
# has the Pillow and pystray packages required by the tray companion.
$pythonCandidates = @(
    $env:ARIADNE_PYTHON,
    'C:\Users\Warren\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe',
    (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe')
) | Where-Object { $_ }

$python = $null
foreach ($candidate in $pythonCandidates) {
    if (Test-Path -LiteralPath $candidate -PathType Leaf) {
        $python = (Resolve-Path -LiteralPath $candidate).Path
        break
    }
}

if (-not $python) {
    $command = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($command) { $python = $command.Source }
}

if (-not $python) {
    throw 'No usable Python interpreter was found. Set ARIADNE_PYTHON to a Python executable.'
}

$pythonw = Join-Path (Split-Path -Parent $python) 'pythonw.exe'
$launcher = if (Test-Path -LiteralPath $pythonw -PathType Leaf) { $pythonw } else { $python }

$existing = Get-NetTCPConnection -State Listen -LocalPort 8765 -ErrorAction SilentlyContinue
if (-not $existing) {
    Start-Process -FilePath $launcher -ArgumentList ('"{0}"' -f $tray) -WorkingDirectory $projectRoot -WindowStyle Hidden
}

if ($OpenBrowser) {
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        try {
            $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 1
            if ($response.StatusCode -eq 200) {
                Start-Process $url
                break
            }
        }
        catch { }
        Start-Sleep -Milliseconds 250
    }
}
