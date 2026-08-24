param(
    [switch]$OpenBrowser
)

$ErrorActionPreference = 'Stop'

$controlPlane = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $controlPlane
$vaultRoot = if ($env:ARIADNE_VAULT_ROOT) { (Resolve-Path -LiteralPath $env:ARIADNE_VAULT_ROOT).Path } else { 'D:\Downloads\KnowledgeVault' }
$vaultSystem = Join-Path $vaultRoot '00_System'
if (-not (Test-Path -LiteralPath $vaultSystem -PathType Container)) {
    throw "Configured Ariadne Vault root is unavailable: $vaultRoot"
}
$env:ARIADNE_VAULT_ROOT = $vaultRoot
$catalogueCount = 0
$embeddingDocuments = 0
$embeddingChunks = 0
$cataloguePath = Join-Path $vaultSystem 'library.json'
$embeddingPath = Join-Path $vaultSystem 'Data\embedding-index.json'
if (Test-Path -LiteralPath $cataloguePath) {
    $catalogue = Get-Content -LiteralPath $cataloguePath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($catalogue -is [array]) { $catalogueCount = $catalogue.Count }
}
if (Test-Path -LiteralPath $embeddingPath) {
    $embedding = Get-Content -LiteralPath $embeddingPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $entries = @($embedding.entries.PSObject.Properties)
    $embeddingChunks = $entries.Count
    $embeddingDocuments = @($entries | ForEach-Object { $_.Value.path } | Sort-Object -Unique).Count
}
Write-Host "Ariadne Vault root: $vaultRoot" -ForegroundColor Cyan
Write-Host ("Catalogue: {0:N0} records | Embeddings: {1:N0} documents / {2:N0} chunks" -f $catalogueCount, $embeddingDocuments, $embeddingChunks) -ForegroundColor Cyan
$tray = Join-Path $controlPlane 'tray.py'
$url = 'http://localhost:8765/'

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

Start-Process -FilePath $launcher -ArgumentList ('"{0}"' -f $tray) -WorkingDirectory $projectRoot -WindowStyle Hidden

if ($OpenBrowser) {
    $opened = $false
    for ($attempt = 0; $attempt -lt 120; $attempt++) {
        try {
            $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 1
            if ($response.StatusCode -eq 200) {
                Start-Process $url
                $opened = $true
                break
            }
        }
        catch { }
        Start-Sleep -Milliseconds 250
    }
    if (-not $opened) {
        # Give the user a browser window even if the tray companion is still
        # starting; the page will become available as soon as it binds.
        Start-Process $url
    }
}
