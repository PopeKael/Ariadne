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
$DownloadsOrganizerPath = Join-Path $PSScriptRoot 'Organize-Downloads.ps1'
if (-not (Test-Path -LiteralPath $MenuPath)) {
    throw "Control menu not found: $MenuPath"
}

$Actions = @{
    ingest = @{ Title = 'Process Inbox'; Script = 'Daily-Ingest.ps1'; Arguments = @() }
    embedding_status = @{ Title = 'Check embedding index'; Script = 'Build-Embeddings.ps1'; Arguments = @('-Status') }
    embedding_rebuild = @{ Title = 'Rebuild embedding index'; Script = 'Build-Embeddings.ps1'; Arguments = @('-Rebuild') }
    retrieval_evaluation = @{ Title = 'Evaluate retrieval'; Script = 'Evaluate-Retrieval.ps1'; Arguments = @() }
    regression_tests = @{ Title = 'Run rebuild regression tests'; Script = 'Run-Rebuild-Tests.ps1'; Arguments = @() }
    audit_failures = @{ Title = 'Audit failed ingestion'; Script = 'Audit-Failed-Ingestion.ps1'; Arguments = @() }
    downloads_preview = @{ Title = 'Preview Downloads organisation'; ScriptPath = $DownloadsOrganizerPath; Arguments = @('-WhatIf') }
    downloads_apply = @{ Title = 'Organise Downloads'; ScriptPath = $DownloadsOrganizerPath; Arguments = @() }
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
    $ScriptPath = if ($Action.ScriptPath) { $Action.ScriptPath } else { Join-Path $PSScriptRoot $Action.Script }
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
# Windows PowerShell 5.1 targets .NET Framework, which has no Convert.ToHexString.
$Token = ([BitConverter]::ToString($TokenBytes) -replace '-', '').ToLowerInvariant()
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
