<#
.SYNOPSIS
    Publish the Ariadne Knowledge Vault.

.DESCRIPTION
    Reads the canonical KnowledgeMap.md from the system folder and
    publishes:

        Wiki\KnowledgeMap.md
        Wiki\index.md
        Wiki\index.html

    This script NEVER modifies the library or compiler outputs.

.NOTES
    Ariadne Project
#>

[CmdletBinding(SupportsShouldProcess)]
param()

# ----------------------------------------------------------------------
# Locate project folders
# ----------------------------------------------------------------------

$ProjectRoot = Split-Path $PSScriptRoot -Parent

$SystemFolder  = Join-Path $ProjectRoot "00_System"
$WikiFolder    = Join-Path $ProjectRoot "Wiki"

$KnowledgeMapSource = Join-Path $SystemFolder "KnowledgeMap.md"

$KnowledgeMapDest = Join-Path $WikiFolder "KnowledgeMap.md"
$MarkdownIndex   = Join-Path $WikiFolder "index.md"
$HtmlIndex       = Join-Path $WikiFolder "index.html"

if (!(Test-Path $KnowledgeMapSource))
{
    throw "KnowledgeMap.md not found:`n$KnowledgeMapSource"
}

Write-Host ""
Write-Host "=== Ariadne Publisher ===" -ForegroundColor Cyan
Write-Host ""

# ----------------------------------------------------------------------
# Publish Knowledge Map
# ----------------------------------------------------------------------

if ($PSCmdlet.ShouldProcess($KnowledgeMapDest, "Publish UTF-8 copy of KnowledgeMap.md"))
{
    Get-Content $KnowledgeMapSource -Raw -Encoding UTF8 |
        Set-Content `
            $KnowledgeMapDest `
            -Encoding UTF8
    Write-Host "Published KnowledgeMap.md"
}

# ----------------------------------------------------------------------
# Parse Knowledge Map
# ----------------------------------------------------------------------

$Lines = Get-Content $KnowledgeMapSource -Encoding UTF8

$Sections = @()

$current = $null

foreach($line in $Lines)
{
    if($line -match '^##\s+(.+)$')
    {
        if($null -ne $current)
        {
            $Sections += $current
        }

        $current = [ordered]@{
            Name = $Matches[1]
            Purpose = ""
            Items = @()
        }

        continue
    }

    if($null -eq $current)
    {
        continue
    }

    if($line.StartsWith("Purpose:"))
    {
        $current.Purpose = $line.Substring(8).Trim()
        continue
    }

    if($line.StartsWith("- "))
    {
        $current.Items += $line.Substring(2)
    }
}

if($null -ne $current)
{
    $Sections += $current
}

# ----------------------------------------------------------------------
# Build Markdown Index
# ----------------------------------------------------------------------

$md = New-Object System.Text.StringBuilder

$null = $md.AppendLine("# Ariadne Knowledge Vault")
$null = $md.AppendLine("")
$null = $md.AppendLine("> **Automatically generated**")
$null = $md.AppendLine("")
$null = $md.AppendLine("Generated: $(Get-Date)")
$null = $md.AppendLine("")
$null = $md.AppendLine("---")
$null = $md.AppendLine("")
$null = $md.AppendLine("## Browse")
$null = $md.AppendLine("")

foreach($section in $Sections)
{
    $anchor = $section.Name.Replace("&","").Replace(" ","-")

    $null = $md.AppendLine("- [$($section.Name)](#$anchor)")
}

$null = $md.AppendLine("")
$null = $md.AppendLine("---")
$null = $md.AppendLine("")

foreach($section in $Sections)
{
    $anchor = $section.Name.Replace("&","").Replace(" ","-")

    $null = $md.AppendLine("<a id=""$anchor""></a>")
    $null = $md.AppendLine("")
    $null = $md.AppendLine("## $($section.Name)")
    $null = $md.AppendLine("")

    if($section.Purpose)
    {
        $null = $md.AppendLine("**Purpose:** $($section.Purpose)")
        $null = $md.AppendLine("")
    }

    foreach($item in $section.Items)
    {
        $null = $md.AppendLine("- $item")
    }

    $null = $md.AppendLine("")
    $null = $md.AppendLine("[Back to top](#ariadne-knowledge-vault)")
    $null = $md.AppendLine("")
}

if ($PSCmdlet.ShouldProcess($MarkdownIndex, "Generate Markdown index"))
{
    $md.ToString() |
        Set-Content `
            $MarkdownIndex `
            -Encoding UTF8
    Write-Host "Generated index.md"
}

# ----------------------------------------------------------------------
# Build HTML Index
# ----------------------------------------------------------------------

$html = New-Object System.Text.StringBuilder

$null = $html.AppendLine("<!DOCTYPE html>")
$null = $html.AppendLine("<html>")
$null = $html.AppendLine("<head>")
$null = $html.AppendLine("<meta charset=""utf-8"">")
$null = $html.AppendLine("<title>Ariadne Knowledge Vault</title>")

$null = $html.AppendLine("<style>")
$null = $html.AppendLine("body{font-family:Segoe UI,Arial,sans-serif;max-width:1200px;margin:auto;padding:40px;line-height:1.6}")
$null = $html.AppendLine("h1{border-bottom:2px solid #999}")
$null = $html.AppendLine("h2{margin-top:40px}")
$null = $html.AppendLine("input{width:100%;padding:10px;font-size:16px;margin-bottom:20px}")
$null = $html.AppendLine("</style>")

$null = $html.AppendLine("<script>")
$null = $html.AppendLine(@"
function filterSections(){
    let q=document.getElementById('search').value.toLowerCase();
    let s=document.getElementsByClassName('section');

    for(let i=0;i<s.length;i++){
        let t=s[i].innerText.toLowerCase();
        s[i].style.display=t.indexOf(q)>=0?'block':'none';
    }
}
"@)
$null = $html.AppendLine("</script>")

$null = $html.AppendLine("</head>")
$null = $html.AppendLine("<body>")

$null = $html.AppendLine("<h1>Ariadne Knowledge Vault</h1>")
$null = $html.AppendLine("<p><em>Automatically generated</em></p>")
$null = $html.AppendLine("<p>Generated: $(Get-Date)</p>")

$null = $html.AppendLine("<input id='search' onkeyup='filterSections()' placeholder='Search...'>")

foreach($section in $Sections)
{
    $safeName = [System.Net.WebUtility]::HtmlEncode($section.Name)
    $safePurpose = [System.Net.WebUtility]::HtmlEncode($section.Purpose)

    $null = $html.AppendLine("<div class='section'>")
    $null = $html.AppendLine("<h2>$safeName</h2>")

    if($section.Purpose)
    {
        $null = $html.AppendLine("<p><strong>Purpose:</strong> $safePurpose</p>")
    }

    $null = $html.AppendLine("<ul>")

    foreach($item in $section.Items)
    {
        $safe = [System.Net.WebUtility]::HtmlEncode($item)
        $null = $html.AppendLine("<li>$safe</li>")
    }

    $null = $html.AppendLine("</ul>")
    $null = $html.AppendLine("</div>")
}

$null = $html.AppendLine("</body>")
$null = $html.AppendLine("</html>")

if ($PSCmdlet.ShouldProcess($HtmlIndex, "Generate HTML index"))
{
    $html.ToString() |
        Set-Content `
            $HtmlIndex `
            -Encoding UTF8
    Write-Host "Generated index.html"
}

Write-Host ""
Write-Host "Publish complete." -ForegroundColor Green
Write-Host ""
