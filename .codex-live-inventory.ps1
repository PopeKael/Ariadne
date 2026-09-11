$ErrorActionPreference = 'Stop'
function Get-Manifest([string]$root) {
  $map = @{}
  foreach ($file in Get-ChildItem -LiteralPath $root -File -Recurse) {
    $relative = $file.FullName.Substring($root.Length + 1)
    if ($relative -match '\\(__pycache__|data|\.git)\\' -or $relative -match '\.(pyc|sqlite3)$') { continue }
    $map[$relative] = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
  }
  return $map
}
function Compare-Manifests([string]$name, [string]$localRoot, [string]$remoteRoot) {
  $local = Get-Manifest $localRoot
  $remote = Get-Manifest $remoteRoot
  $all = @($local.Keys + $remote.Keys | Sort-Object -Unique)
  $mismatches = @()
  $localOnly = @()
  $remoteOnly = @()
  foreach ($path in $all) {
    if (-not $local.ContainsKey($path)) { $remoteOnly += $path; continue }
    if (-not $remote.ContainsKey($path)) { $localOnly += $path; continue }
    if ($local[$path] -ne $remote[$path]) { $mismatches += $path }
  }
  Write-Output "$name LOCAL_FILES=$($local.Count) REMOTE_FILES=$($remote.Count) MISMATCHES=$($mismatches.Count) LOCAL_ONLY=$($localOnly.Count) REMOTE_ONLY=$($remoteOnly.Count)"
  foreach ($path in $mismatches) { Write-Output "$name MISMATCH $path" }
  foreach ($path in $localOnly) { Write-Output "$name LOCAL_ONLY $path" }
  foreach ($path in $remoteOnly) { Write-Output "$name REMOTE_ONLY $path" }
}
Compare-Manifests 'DISCOVERY' 'D:\Downloads\Ariadne\discovery-service' '\\kstore\docker\ariadne-discovery-service\discovery-service'
Compare-Manifests 'SIGNAL' 'D:\Downloads\Ariadne\signal-service' '\\kstore\docker\ariadne-signal-service'
Write-Output 'DEPLOYED_DISCOVERY_COMPOSE'
Get-Content -LiteralPath '\\kstore\docker\ariadne-discovery-service\discovery-service\docker-compose.yml' -ErrorAction SilentlyContinue
Write-Output 'DEPLOYED_SIGNAL_COMPOSE'
Get-Content -LiteralPath '\\kstore\docker\ariadne-signal-service\docker-compose.yml' -ErrorAction SilentlyContinue
