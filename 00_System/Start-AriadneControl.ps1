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
$QueryPagePath = Join-Path $PSScriptRoot 'Ariadne-Query.html'
$McpPath = Join-Path $PSScriptRoot 'ariadne_mcp.py'
$ProcessedRoot = (Join-Path $Vault 'Processed').TrimEnd('\')
$QueryJobsRoot = Join-Path $PSScriptRoot 'Data\QueryJobs'
$DownloadsOrganizerPath = Join-Path $PSScriptRoot 'Organize-Downloads.ps1'
if (-not (Test-Path -LiteralPath $MenuPath)) {
    throw "Control menu not found: $MenuPath"
}
if (-not (Test-Path -LiteralPath $QueryPagePath)) {
    throw "Query page not found: $QueryPagePath"
}
if (-not (Test-Path -LiteralPath $McpPath)) {
    throw "MCP server not found: $McpPath"
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

function Invoke-AriadneQuery {
    param(
        [Parameter(Mandatory)] [string]$Query,
        [ValidateRange(1, 20)] [int]$Limit = 8,
        [string]$ToolName = 'search_knowledge_chunks'
    )

    $Python = Get-Command py -ErrorAction SilentlyContinue
    $Arguments = '-3 "{0}"' -f $McpPath
    if (-not $Python) {
        $Python = Get-Command python -ErrorAction Stop
        $Arguments = '"{0}"' -f $McpPath
    }

    $Request = @{
        jsonrpc = '2.0'
        id = 1
        method = 'tools/call'
        params = @{
            name = $ToolName
            arguments = @{ query = $Query; limit = $Limit }
        }
    } | ConvertTo-Json -Compress -Depth 10

    $StartInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $StartInfo.FileName = $Python.Source
    $StartInfo.Arguments = $Arguments
    $StartInfo.WorkingDirectory = $Vault
    $StartInfo.UseShellExecute = $false
    $StartInfo.CreateNoWindow = $true
    $StartInfo.RedirectStandardInput = $true
    $StartInfo.RedirectStandardOutput = $true
    $StartInfo.RedirectStandardError = $true
    $StartInfo.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $StartInfo.StandardErrorEncoding = [System.Text.Encoding]::UTF8
    $Process = [System.Diagnostics.Process]::new()
    $Process.StartInfo = $StartInfo
    if (-not $Process.Start()) { throw 'Could not start the MCP query process.' }
    $Process.StandardInput.WriteLine($Request)
    $Process.StandardInput.Close()
    $Output = $Process.StandardOutput.ReadToEnd()
    $ErrorOutput = $Process.StandardError.ReadToEnd()
    $Process.WaitForExit()
    if ($Process.ExitCode -ne 0) {
        throw "MCP query process failed: $ErrorOutput"
    }

    $ResponseLine = ($Output -split "`r?`n" | Where-Object { $_.Trim() } | Select-Object -Last 1)
    if (-not $ResponseLine) { throw 'MCP query returned no response.' }
    $Response = $ResponseLine | ConvertFrom-Json
    if ($Response.error) { throw [string]$Response.error.message }
    $Text = $Response.result.content[0].text
    if (-not $Text) { throw 'MCP query returned no result content.' }
    return ($Text | ConvertFrom-Json)
}

function Start-AriadnePlannedJob {
    param([Parameter(Mandatory)] [string]$Query, [int]$Limit = 6, [string]$Mode = 'answer')
    if (-not (Test-Path -LiteralPath $QueryJobsRoot)) {
        New-Item -ItemType Directory -Path $QueryJobsRoot -Force | Out-Null
    }
    $JobId = [guid]::NewGuid().ToString('N')
    $JobPath = Join-Path $QueryJobsRoot "$JobId.json"
    $StatusPath = "$JobPath.status.json"
    @{ query = $Query; limit = $Limit; mode = $Mode; session = $Token } | ConvertTo-Json -Compress | Set-Content -LiteralPath $JobPath -Encoding UTF8
    @{ state = 'queued'; stage = 'queued'; message = 'Queued for the local librarian…'; completed = 0; total = 0 } |
        ConvertTo-Json -Compress | Set-Content -LiteralPath $StatusPath -Encoding UTF8

    $Python = Get-Command py -ErrorAction SilentlyContinue
    if ($Python) {
        $ArgumentList = @('-3', $McpPath, '--planned-job', $JobPath)
    } else {
        $Python = Get-Command python -ErrorAction Stop
        $ArgumentList = @($McpPath, '--planned-job', $JobPath)
    }
    Start-Process -FilePath $Python.Source -ArgumentList $ArgumentList -WorkingDirectory $Vault -WindowStyle Hidden | Out-Null
    return $JobId
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

            if ($Context.Request.HttpMethod -eq 'GET' -and $Path -eq '/query') {
                $Page = (Get-Content -LiteralPath $QueryPagePath -Raw).Replace('__ARIADNE_TOKEN__', $Token)
                Send-Response $Context 200 'text/html; charset=utf-8' $Page
                continue
            }

            if ($Context.Request.HttpMethod -eq 'GET' -and $Path -eq '/planned-status' -and $Context.Request.QueryString['token'] -eq $Token) {
                $JobId = $Context.Request.QueryString['job_id']
                if ($JobId -notmatch '^[0-9a-f]{32}$') {
                    Send-Response $Context 400 'application/json; charset=utf-8' '{"ok":false,"message":"A valid job id is required."}'
                    continue
                }
                $StatusPath = Join-Path $QueryJobsRoot "$JobId.json.status.json"
                if (-not (Test-Path -LiteralPath $StatusPath -PathType Leaf)) {
                    Send-Response $Context 404 'application/json; charset=utf-8' '{"ok":false,"message":"Planned query job not found."}'
                    continue
                }
                $Status = Get-Content -LiteralPath $StatusPath -Raw -Encoding UTF8
                Send-Response $Context 200 'application/json; charset=utf-8' $Status
                continue
            }

            if ($Context.Request.HttpMethod -eq 'GET' -and $Path -eq '/source' -and $Context.Request.QueryString['token'] -eq $Token) {
                $RelativePath = $Context.Request.QueryString['path']
                if (-not $RelativePath) {
                    Send-Response $Context 400 'text/plain; charset=utf-8' 'A source path is required.'
                    continue
                }
                $Candidate = [System.IO.Path]::GetFullPath((Join-Path $Vault $RelativePath))
                if (-not $Candidate.StartsWith($ProcessedRoot + '\', [System.StringComparison]::OrdinalIgnoreCase) -or -not (Test-Path -LiteralPath $Candidate -PathType Leaf)) {
                    Send-Response $Context 404 'text/plain; charset=utf-8' 'Source note not found.'
                    continue
                }
                $Title = [System.IO.Path]::GetFileName($Candidate)
                $Content = [System.Net.WebUtility]::HtmlEncode((Get-Content -LiteralPath $Candidate -Raw -Encoding UTF8))
                $TitleHtml = [System.Net.WebUtility]::HtmlEncode($Title)
                $Page = @"
<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>$TitleHtml — Ariadne Source</title><style>body{max-width:1000px;margin:0 auto;padding:32px 24px 64px;background:#10151b;color:#e7edf4;font:16px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}a{color:#6bc4e5}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#17212b;border:1px solid #314152;border-radius:9px;padding:20px}section{margin-top:24px;background:#17212b;border:1px solid #314152;border-radius:9px;padding:17px}</style></head><body><p><a href="/query?token=$Token">← Knowledge Query</a></p><h1>$TitleHtml</h1><pre id="source-content">$Content</pre><section><h2>Related external links</h2><ul id="external-links"><li>Scanning source note…</li></ul></section><script>
const pre=document.getElementById('source-content');
const list=document.getElementById('external-links');
const pattern=/https?:\/\/[^\s<>"\)\]]+/gi;
const raw=pre.textContent;
const matches=[];
let match;
while((match=pattern.exec(raw))!==null){let url=match[0].replace(/[.,;:!?]+$/,'');if(!matches.includes(url))matches.push(url);}
pre.textContent='';
let position=0;
for(const url of matches){const index=raw.indexOf(url,position);pre.append(document.createTextNode(raw.slice(position,index)));const anchor=document.createElement('a');anchor.href=url;anchor.target='_blank';anchor.rel='noopener';anchor.textContent=url;pre.append(anchor);position=index+url.length;}
pre.append(document.createTextNode(raw.slice(position)));
list.textContent='';
if(!matches.length){list.innerHTML='<li>No external links found in this note.</li>';}else{for(const url of matches){const item=document.createElement('li');const anchor=document.createElement('a');anchor.href=url;anchor.target='_blank';anchor.rel='noopener';anchor.textContent=url;item.append(anchor);list.append(item);}}
</script></body></html>
"@
                Send-Response $Context 200 'text/html; charset=utf-8' $Page
                continue
            }

            if ($Context.Request.HttpMethod -eq 'POST' -and $Path -eq '/planned-query' -and $Context.Request.Headers['X-Ariadne-Token'] -eq $Token) {
                $Reader = [System.IO.StreamReader]::new($Context.Request.InputStream, $Context.Request.ContentEncoding)
                $Request = $Reader.ReadToEnd() | ConvertFrom-Json
                $Reader.Close()
                if (-not $Request.query -or $Request.query -isnot [string] -or [string]::IsNullOrWhiteSpace($Request.query)) {
                    Send-Response $Context 400 'application/json; charset=utf-8' '{"ok":false,"message":"A non-empty question is required."}'
                    continue
                }
                $Limit = if ($Request.limit) { [int]$Request.limit } else { 6 }
                if ($Limit -lt 1 -or $Limit -gt 12) {
                    Send-Response $Context 400 'application/json; charset=utf-8' '{"ok":false,"message":"Limit must be between 1 and 12."}'
                    continue
                }
                $Mode = if ($Request.mode -eq 'summary') { 'summary' } else { 'answer' }
                $JobId = Start-AriadnePlannedJob -Query $Request.query.Trim() -Limit $Limit -Mode $Mode
                $Body = @{ ok = $true; job_id = $JobId } | ConvertTo-Json -Compress
                Send-Response $Context 202 'application/json; charset=utf-8' $Body
                continue
            }

            if ($Context.Request.HttpMethod -eq 'POST' -and ($Path -eq '/query' -or $Path -eq '/summarize') -and $Context.Request.Headers['X-Ariadne-Token'] -eq $Token) {
                $Reader = [System.IO.StreamReader]::new($Context.Request.InputStream, $Context.Request.ContentEncoding)
                $Request = $Reader.ReadToEnd() | ConvertFrom-Json
                $Reader.Close()
                if (-not $Request.query -or $Request.query -isnot [string] -or [string]::IsNullOrWhiteSpace($Request.query)) {
                    Send-Response $Context 400 'application/json; charset=utf-8' '{"ok":false,"message":"A non-empty query is required."}'
                    continue
                }
                $Limit = if ($Request.limit) { [int]$Request.limit } else { 8 }
                if ($Limit -lt 1 -or $Limit -gt 20) {
                    Send-Response $Context 400 'application/json; charset=utf-8' '{"ok":false,"message":"Limit must be between 1 and 20."}'
                    continue
                }
                $ToolName = if ($Path -eq '/summarize') { 'summarize_knowledge' } else { 'search_knowledge_chunks' }
                $Result = Invoke-AriadneQuery -Query $Request.query.Trim() -Limit $Limit -ToolName $ToolName
                if ($Path -eq '/summarize') {
                    $Body = @{ ok = $true; query = $Result.query; summary = $Result.summary; sources = @($Result.sources); model = $Result.model; identity_kernel = $Result.identity_kernel } | ConvertTo-Json -Depth 20 -Compress
                } else {
                    $Body = @{ ok = $true; query = $Result.query; match_count = $Result.match_count; results = @($Result.results) } | ConvertTo-Json -Depth 20 -Compress
                }
                Send-Response $Context 200 'application/json; charset=utf-8' $Body
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
