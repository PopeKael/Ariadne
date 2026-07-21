<#
.SYNOPSIS
    Starts the local Ariadne Control menu.

.DESCRIPTION
    Serves Ariadne-Control.html on loopback and launches only explicitly
    allow-listed PowerShell workflows. Press Ctrl+C in this window to stop it.
#>
[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8787,
    [switch]$NoBrowser
)

$Vault = Split-Path $PSScriptRoot -Parent
$MenuPath = Join-Path $PSScriptRoot 'Ariadne-Control.html'
if (-not (Test-Path -LiteralPath $MenuPath)) {
    throw "Control menu not found: $MenuPath"
}

$Actions = @{
    ingest = @{ Title = 'Process Inbox'; Script = 'Run Injest.ps1'; Arguments = @() }
    reclassify_all = @{ Title = 'Reclassify entire vault'; Script = 'Reclassify-All.ps1'; Arguments = @() }
    reclassify_status = @{ Title = 'Reclassification status'; Script = 'Reclassification-Status.ps1'; Arguments = @() }
    retry_failed = @{ Title = 'Retry failed ingestion'; Script = 'Retry-FailedIngestion.ps1'; Arguments = @() }
    compile_proposal = @{ Title = 'Create knowledge-link proposals'; Script = 'Compile-Knowledge.ps1'; Arguments = @('-Mode', 'Proposal') }
    graph_health = @{ Title = 'Run graph health audit'; Script = 'GraphHealth.ps1'; Arguments = @() }
    publish = @{ Title = 'Publish knowledge views'; Script = 'Publish-Knowledge.ps1'; Arguments = @() }
    embedding_status = @{ Title = 'Check embedding index'; Script = 'Build-Embeddings.ps1'; Arguments = @('-Status') }
    embedding_rebuild = @{ Title = 'Rebuild embedding index'; Script = 'Build-Embeddings.ps1'; Arguments = @('-Rebuild') }
    reconcile_graph = @{ Title = 'Reconcile graph'; Script = 'Reconcile-Graph.ps1'; Arguments = @() }
    rebuild_graph = @{ Title = 'Rebuild graph relationships'; Script = 'Rebuild-GraphRelations.ps1'; Arguments = @() }
    repair_retry_queue = @{ Title = 'Repair retry queue'; Script = 'Repair-RetryQueue.ps1'; Arguments = @() }
    migrate_legacy_graph = @{ Title = 'Migrate legacy graph'; Script = 'Migrate-LegacyGraph.ps1'; Arguments = @() }
    migrate_people = @{ Title = 'Migrate person entities'; Script = 'Migrate-PersonEntities.ps1'; Arguments = @() }
    snapshot = @{ Title = 'Commit and push snapshot'; Script = 'Commit.ps1'; Arguments = @() }
}

function Send-Response {
    param($Context, [int]$StatusCode, [string]$ContentType, [string]$Body)
    $Bytes = [System.Text.Encoding]::UTF8.GetBytes($Body)
    $Context.Response.StatusCode = $StatusCode
    $Context.Response.ContentType = $ContentType
    $Context.Response.ContentEncoding = [System.Text.Encoding]::UTF8
    $Context.Response.ContentLength64 = $Bytes.Length
    $Context.Response.OutputStream.Write($Bytes, 0, $Bytes.Length)
    $Context.Response.Close()
}

function Start-AriadneAction {
    param([hashtable]$Action)
    $ScriptPath = Join-Path $PSScriptRoot $Action.Script
    if (-not (Test-Path -LiteralPath $ScriptPath)) {
        throw "Workflow script not found: $ScriptPath"
    }

    $Shell = Get-Command pwsh -ErrorAction SilentlyContinue
    if (-not $Shell) { $Shell = Get-Command powershell -ErrorAction Stop }
    $ArgumentLine = '-NoExit -ExecutionPolicy Bypass -File "{0}" {1}' -f $ScriptPath, ($Action.Arguments -join ' ')
    Start-Process -FilePath $Shell.Source -ArgumentList $ArgumentLine -WorkingDirectory $Vault
}

$TokenBytes = New-Object byte[] 32
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($TokenBytes)
$Token = [Convert]::ToHexString($TokenBytes).ToLowerInvariant()
$Listener = [System.Net.HttpListener]::new()
$Listener.Prefixes.Add("http://127.0.0.1:$Port/")

try {
    $Listener.Start()
    $Url = "http://127.0.0.1:$Port/?token=$Token"
    Write-Host "Ariadne Control is running at $Url" -ForegroundColor Cyan
    Write-Host 'Press Ctrl+C here to stop the control menu.' -ForegroundColor DarkGray
    if (-not $NoBrowser) { Start-Process $Url }

    $PendingContext = $Listener.BeginGetContext($null, $null)
    while ($Listener.IsListening) {
        # Do not block indefinitely in GetContext(): PowerShell can then miss
        # Ctrl+C until a browser makes another request.
        if (-not $PendingContext.AsyncWaitHandle.WaitOne(250)) { continue }
        $Context = $Listener.EndGetContext($PendingContext)
        $PendingContext = $Listener.BeginGetContext($null, $null)
        try {
            $Path = $Context.Request.Url.AbsolutePath
            if ($Context.Request.HttpMethod -eq 'GET' -and $Path -eq '/') {
                $Page = (Get-Content -LiteralPath $MenuPath -Raw).Replace('__ARIADNE_TOKEN__', $Token)
                Send-Response $Context 200 'text/html; charset=utf-8' $Page
                continue
            }

            if ($Context.Request.HttpMethod -ne 'POST' -or $Path -ne '/run' -or $Context.Request.Headers['X-Ariadne-Token'] -ne $Token) {
                Send-Response $Context 404 'text/plain; charset=utf-8' 'Not found.'
                continue
            }

            $Reader = [System.IO.StreamReader]::new($Context.Request.InputStream, $Context.Request.ContentEncoding)
            $Request = $Reader.ReadToEnd() | ConvertFrom-Json
            $Reader.Close()
            $Action = $Actions[$Request.action]
            if (-not $Action) {
                Send-Response $Context 400 'application/json; charset=utf-8' '{"ok":false,"message":"Unknown action."}'
                continue
            }

            Start-AriadneAction $Action
            Write-Host "Launched: $($Action.Title)" -ForegroundColor Green
            $Body = @{ ok = $true; message = "$($Action.Title) started in a new PowerShell window." } | ConvertTo-Json -Compress
            Send-Response $Context 200 'application/json; charset=utf-8' $Body
        }
        catch {
            Write-Warning $_.Exception.Message
            if ($Context.Response.OutputStream.CanWrite) {
                $Body = @{ ok = $false; message = $_.Exception.Message } | ConvertTo-Json -Compress
                Send-Response $Context 500 'application/json; charset=utf-8' $Body
            }
        }
    }
}
finally {
    if ($Listener.IsListening) { $Listener.Stop() }
    $Listener.Close()
}
