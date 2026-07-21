[CmdletBinding()]
param(
    [string]$Vault = 'D:\Downloads\KnowledgeVault',
    [string]$OutputPath = 'Logs\Controlled-Classification-20260721.json'
)

$ErrorActionPreference = 'Stop'
$system = Join-Path $Vault '00_System'
$libraryPath = Join-Path $system 'library.json'
$domainsPath = Join-Path $system 'DomainVocabulary.json'
$library = @(Get-Content -LiteralPath $libraryPath -Raw | ConvertFrom-Json)
$domains = Get-Content -LiteralPath $domainsPath -Raw | ConvertFrom-Json
$allowed = @($domains.domains | Where-Object { $_.name -ne 'Archive' } | ForEach-Object { $_.name })
$catalog = (($domains.domains | Where-Object { $_.name -ne 'Archive' } | ForEach-Object { "- $($_.name): $($_.description)" }) -join [Environment]::NewLine)
$record = $library | Select-Object -First 1
$evidence = @(
    "Source: $($record.source_name)"
    "Map entry: $($record.map_entry)"
    "Summary: $($record.summary)"
    "Tags: $(@($record.tags) -join ', ')"
    "Subtopics: $(@($record.subtopics) -join ', ')"
    "Entities: $(@($record.entities) -join ', ')"
) -join [Environment]::NewLine
$prompt = @(
    'Reclassify one existing KnowledgeVault record. Return only JSON: {"primary_topic":"domain","secondary_domains":[],"reason":"short reason"}'
    'Use only the supplied domains. Do not summarise or create tags, links, or entities.'
    'DOMAINS:'
    $catalog
    'EVIDENCE:'
    $evidence
) -join [Environment]::NewLine
$request = [ordered]@{
    model = 'gpt-oss:20b'
    prompt = $prompt
    stream = $false
    format = 'json'
    options = [ordered]@{ temperature = 0; num_predict = 220 }
}
$requestBody = $request | ConvertTo-Json -Depth 8
$webResponse = Invoke-WebRequest -Uri 'http://localhost:11434/api/generate' -Method Post -ContentType 'application/json' -Body $requestBody
$rawResponseText = [string]$webResponse.Content
$parsedResponse = $rawResponseText | ConvertFrom-Json
$artifact = [ordered]@{
    captured_at = (Get-Date).ToString('o')
    endpoint = 'http://localhost:11434/api/generate'
    source_record = $record.source_name
    request_body = $requestBody
    response_status_code = [int]$webResponse.StatusCode
    response_headers = [ordered]@{}
    raw_response_text = $rawResponseText
    parsed_response = $parsedResponse
}
foreach ($header in $webResponse.Headers.Keys) { $artifact.response_headers[$header] = [string]$webResponse.Headers[$header] }
$destination = Join-Path $Vault $OutputPath
$artifact | ConvertTo-Json -Depth 20 | Out-File -LiteralPath $destination -Encoding utf8 -NoNewline
Write-Output $destination
