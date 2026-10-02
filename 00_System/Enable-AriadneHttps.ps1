<# Enable the prepared Ariadne-owned TLS listener with a Hera-only firewall rule. #>
[CmdletBinding(SupportsShouldProcess = $true)]
param([ValidateSet('Plan', 'Apply')][string]$Mode = 'Plan')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$tlsRoot = Join-Path $projectRoot 'Data\tls'
$prepared = Join-Path $tlsRoot 'https-gateway.prepared.json'
$active = Join-Path $tlsRoot 'https-gateway.json'
$ruleName = 'Ariadne-HTTPS-From-Hera'
$hostsPath = Join-Path $env:SystemRoot 'System32\drivers\etc\hosts'
$canonicalHost = 'ariadne.dia.net.au'
$settings = Get-Content -LiteralPath $prepared -Raw | ConvertFrom-Json
if ($settings.bind -ne '192.168.1.100' -or $settings.port -ne 18765 -or
    (@($settings.allowed_peers) -join ',') -ne '192.168.1.200,192.168.1.100' -or
    $settings.core_url -ne 'http://127.0.0.1:8765' -or $settings.ollama_url -ne 'http://127.0.0.1:11434') {
    throw 'Prepared settings do not match the reviewed private HTTPS scope.'
}
foreach ($path in @($settings.certificate, $settings.private_key)) {
    $resolved = (Resolve-Path -LiteralPath $path).Path
    if (-not $resolved.StartsWith($tlsRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Certificate files must stay in the private TLS directory.'
    }
}
$plan = [ordered]@{
    desktop_tls = '192.168.1.100:18765'
    firewall_remote = '192.168.1.200 only'
    firewall_profile = 'Private'
    rule = $ruleName
    certificate_name = 'ariadne.dia.net.au'
    ollama_access = 'nomic-embed-text availability and embeddings only; raw Ollama remains loopback'
    lifecycle = 'Existing Ariadne core owns the TLS listener; no additional process/container'
    private_browser_routing = 'Windows hosts entry: ariadne.dia.net.au -> 192.168.1.200; Cloudflare unchanged'
    activation = 'Configuration plus firewall rule; core restart required separately'
}
if ($Mode -eq 'Plan') { $plan | ConvertTo-Json; return }
if (-not $PSCmdlet.ShouldProcess($plan.desktop_tls, 'Enable persistent HTTPS access from Hera only')) { return }
$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Application requires an Administrator PowerShell.'
}
if (-not (Get-NetIPAddress -AddressFamily IPv4 -IPAddress $settings.bind -ErrorAction SilentlyContinue)) {
    throw 'The reviewed desktop address is not assigned.'
}
$hostMappings = @(Get-Content -LiteralPath $hostsPath | ForEach-Object {
    $parts = (($_ -split '#', 2)[0].Trim() -split '\s+')
    if ($parts.Count -gt 1 -and $parts[1..($parts.Count - 1)] -contains $canonicalHost) { $parts[0] }
})
if ($hostMappings | Where-Object { $_ -ne '192.168.1.200' }) {
    throw 'A conflicting local hostname override exists; nothing was changed.'
}
if (Test-Path -LiteralPath $active) {
    if ((Get-Content -LiteralPath $active -Raw) -ne (Get-Content -LiteralPath $prepared -Raw)) {
        throw 'A different active TLS configuration exists; nothing was changed.'
    }
}
$rule = Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue
if ($rule) {
    $addresses = $rule | Get-NetFirewallAddressFilter
    $ports = $rule | Get-NetFirewallPortFilter
    if ($rule.Direction -ne 'Inbound' -or $rule.Action -ne 'Allow' -or $rule.Profile -ne 'Private' -or
        @($addresses.LocalAddress).Count -ne 1 -or $addresses.LocalAddress -ne $settings.bind -or
        @($addresses.RemoteAddress).Count -ne 1 -or $addresses.RemoteAddress -ne '192.168.1.200' -or
        $ports.Protocol -ne 'TCP' -or $ports.LocalPort -ne '18765') {
        throw 'The existing named firewall rule differs; nothing was changed.'
    }
}
$created = $false
$hostsChanged = $false
$hostsHash = $null
$hostsBackup = Join-Path $tlsRoot 'hosts.before-https.txt'
try {
    if (-not $rule) {
        New-NetFirewallRule -Name $ruleName -DisplayName 'Ariadne HTTPS from Hera only' -Direction Inbound -Action Allow `
            -Protocol TCP -LocalAddress $settings.bind -LocalPort 18765 -RemoteAddress '192.168.1.200' `
            -Profile Private -EdgeTraversalPolicy Block | Out-Null
        $created = $true
    }
    if (-not $hostMappings.Count) {
        if (Test-Path -LiteralPath $hostsBackup) { throw 'An earlier hosts backup exists; preserve it and review before retrying.' }
        Copy-Item -LiteralPath $hostsPath -Destination $hostsBackup
        Add-Content -LiteralPath $hostsPath -Value "`r`n192.168.1.200 $canonicalHost # Ariadne private HTTPS" -Encoding ASCII
        $hostsChanged = $true
        $hostsHash = (Get-FileHash -LiteralPath $hostsPath).Hash
    }
    Copy-Item -LiteralPath $prepared -Destination $active
} catch {
    if ($hostsChanged -and (Get-FileHash -LiteralPath $hostsPath).Hash -eq $hostsHash) {
        Copy-Item -LiteralPath $hostsBackup -Destination $hostsPath
    }
    if ($created) { Remove-NetFirewallRule -Name $ruleName }
    throw
}
Write-Output 'Private TLS configuration enabled. Restart the Ariadne core and verify from Hera before changing proxy routing.'
