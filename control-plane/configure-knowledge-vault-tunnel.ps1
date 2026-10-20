param(
    [Parameter(Mandatory = $false)]
    [string]$TunnelId
)

$ErrorActionPreference = 'Stop'
$pattern = '^tunnel_(?:[a-z0-9]{32}|[a-z0-9]{4}_[a-z0-9]{32})$'
if (-not $TunnelId) {
    $TunnelId = Read-Host 'OpenAI Platform tunnel ID'
}
if ($TunnelId -notmatch $pattern) {
    throw 'Enter the tunnel ID copied from OpenAI Platform Tunnels.'
}

$vaultRoot = if ($env:ARIADNE_VAULT_ROOT) { $env:ARIADNE_VAULT_ROOT } else { 'D:\Downloads\KnowledgeVault' }
$python = Join-Path $vaultRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Knowledge Vault Python environment not found: $python"
}
$credentialStore = Join-Path $PSScriptRoot 'credential_store.py'
$secureKey = Read-Host 'OpenAI runtime API key (Tunnels Read + Use)' -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
try {
    $plainKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    $plainKey | & $python $credentialStore store-stdin OPENAI_TUNNEL_API_KEY
    if ($LASTEXITCODE -ne 0) { throw 'Could not save the runtime key to Windows Credential Manager.' }
}
finally {
    if ($bstr -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
    $plainKey = $null
}

$configPath = if ($env:ARIADNE_CONFIG_PATH) {
    [IO.Path]::GetFullPath($env:ARIADNE_CONFIG_PATH)
} else {
    Join-Path $env:LOCALAPPDATA 'Ariadne\configuration.json'
}
$configDirectory = Split-Path -Parent $configPath
New-Item -ItemType Directory -Path $configDirectory -Force | Out-Null
$config = @{}
if (Test-Path -LiteralPath $configPath -PathType Leaf) {
    $existing = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
    if ($existing -is [System.Management.Automation.PSCustomObject]) {
        foreach ($property in $existing.PSObject.Properties) { $config[$property.Name] = $property.Value }
    }
}
$mcp = @{}
if ($config.knowledge_vault_mcp -is [System.Management.Automation.PSCustomObject]) {
    foreach ($property in $config.knowledge_vault_mcp.PSObject.Properties) { $mcp[$property.Name] = $property.Value }
}
$mcp.tunnel_id = $TunnelId
$mcp.tunnel_client_path = 'D:\Downloads\tunnel-client-v0.0.16-windows-amd64\tunnel-client.exe'
$config.knowledge_vault_mcp = $mcp
$temporary = Join-Path $configDirectory ([IO.Path]::GetRandomFileName())
try {
    $json = $config | ConvertTo-Json -Depth 32
    $utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($temporary, $json, $utf8WithoutBom)
    Move-Item -LiteralPath $temporary -Destination $configPath -Force
}
finally {
    Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
}
Write-Host 'Tunnel ID saved in Ariadne configuration; runtime key saved in Windows Credential Manager.'
Write-Host 'Restart Ariadne to let the Rust Host establish and supervise the MCP tunnel.'
