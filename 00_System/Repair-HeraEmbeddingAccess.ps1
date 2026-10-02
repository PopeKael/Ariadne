<#
Keep Ollama loopback-only and restore Hera's configured TCP endpoint using
a persistent, source-restricted Windows relay. Default mode is read-only.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [ValidateSet('Plan', 'Apply', 'Remove')][string]$Mode = 'Plan',
    [string]$DesktopAddress = '192.168.1.100',
    [string]$HeraAddress = '192.168.1.200'
)
$ErrorActionPreference = 'Stop'
if ($Mode -eq 'Apply') {
    throw 'Superseded by the platform HTTPS requirement. Plain HTTP relay activation is disabled; see docs/https-platform-design-2026-10-01.md.'
}
$Port = 11434
$RuleName = 'Ariadne-Hera-Ollama-Relay'
foreach ($address in @($DesktopAddress, $HeraAddress)) {
    $parsed = [System.Net.IPAddress]::Parse($address)
    if ($parsed.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork -or
        $address -notmatch '^192\.168\.\d{1,3}\.\d{1,3}$') {
        throw 'This repair requires explicit private LAN IPv4 addresses.'
    }
}
if ($DesktopAddress -eq $HeraAddress) { throw 'Desktop and Hera addresses must differ.' }
$proxyArguments = @('interface', 'portproxy', 'add', 'v4tov4',
    "listenaddress=$DesktopAddress", "listenport=$Port", 'connectaddress=127.0.0.1',
    "connectport=$Port", 'protocol=tcp')
$plan = [ordered]@{
    mode = $Mode
    relay = "$DesktopAddress`:$Port -> 127.0.0.1:$Port"
    allowed_source = $HeraAddress
    firewall_rule = $RuleName
    netsh_arguments = $proxyArguments
    ollama_environment_changes = $false
    service_restarts = $false
}
if ($Mode -eq 'Plan') { $plan | ConvertTo-Json -Depth 4; return }
if (-not $PSCmdlet.ShouldProcess($plan.relay, "$Mode Hera-only embedding relay")) { return }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Applying or removing this network relay requires an Administrator PowerShell.'
}
$mapping = @(& netsh interface portproxy show v4tov4)
if ($LASTEXITCODE -ne 0) { throw 'Could not inspect existing forwarding rules.' }
$existing = @($mapping | Where-Object { $_ -match "^\s*$([regex]::Escape($DesktopAddress))\s+$Port\s+" })
if ($existing.Count -gt 1 -or ($existing.Count -eq 1 -and
    $existing[0] -notmatch "\s+127\.0\.0\.1\s+$Port\s*$")) {
    throw 'An unrelated forwarding rule owns this endpoint; nothing was changed.'
}
$rule = Get-NetFirewallRule -Name $RuleName -ErrorAction SilentlyContinue
if ($rule) {
    $addressFilter = $rule | Get-NetFirewallAddressFilter
    $portFilter = $rule | Get-NetFirewallPortFilter
    if ($rule.Direction -ne 'Inbound' -or $rule.Action -ne 'Allow' -or
        @($addressFilter.LocalAddress).Count -ne 1 -or $addressFilter.LocalAddress -ne $DesktopAddress -or
        @($addressFilter.RemoteAddress).Count -ne 1 -or $addressFilter.RemoteAddress -ne $HeraAddress -or
        $portFilter.Protocol -ne 'TCP' -or $portFilter.LocalPort -ne "$Port") {
        throw 'The named firewall rule differs from this repair; nothing was changed.'
    }
}
if ($Mode -eq 'Remove') {
    if ($existing.Count) {
        & netsh interface portproxy delete v4tov4 "listenaddress=$DesktopAddress" "listenport=$Port" protocol=tcp
        if ($LASTEXITCODE -ne 0) { throw 'Could not remove the verified forwarding rule.' }
    }
    if ($rule) { Remove-NetFirewallRule -Name $RuleName }
    Write-Output 'Removed only the verified Hera relay and its firewall rule.'
    return
}
$local = Get-NetIPAddress -AddressFamily IPv4 -IPAddress $DesktopAddress -ErrorAction SilentlyContinue
if (-not $local) { throw 'The configured desktop address is not assigned here.' }
if ((Get-Service iphlpsvc).Status -ne 'Running') { throw 'Windows IP Helper is not running; no service changes were made.' }
$catalogue = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 5
if (-not (@($catalogue.models.name) | Where-Object { $_ -in @('nomic-embed-text', 'nomic-embed-text:latest') })) {
    throw 'The local embedding model is missing; no network changes were made.'
}
$ruleCreated = $false
try {
    if (-not $rule) {
        New-NetFirewallRule -Name $RuleName -DisplayName 'Ariadne embeddings from Hera only' `
            -Direction Inbound -Action Allow -Protocol TCP -LocalAddress $DesktopAddress `
            -LocalPort $Port -RemoteAddress $HeraAddress -Profile Any -EdgeTraversalPolicy Block | Out-Null
        $ruleCreated = $true
    }
    if (-not $existing.Count) {
        & netsh @proxyArguments
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the forwarding rule.' }
    }
} catch {
    if ($ruleCreated) { Remove-NetFirewallRule -Name $RuleName }
    throw
}
Write-Output 'Hera relay installed. Verify model availability and an actual embedding from Hera, then refresh Signal matching.'
Write-Output 'Repeat that acceptance after restarting Ariadne; local-only model checks do not verify the NAS dependency.'
