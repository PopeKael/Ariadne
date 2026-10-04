$Host.UI.RawUI.WindowTitle = 'HERA IMAGE REPAIR - ENTER PASSWORD HERE'
$ErrorActionPreference = 'Stop'
Write-Host 'Hera article image repair. Enter your SSH password, then sudo password when prompted.'
Write-Host 'The update and selective backfill will then run automatically.'
$repairLog = Join-Path $PSScriptRoot '../runtime/article-image-repair-terminal.log'
Start-Transcript -Path $repairLog -Force
ssh -tt -o BatchMode=no -o PubkeyAuthentication=no -o PreferredAuthentications=password Wazza@192.168.1.200 'sudo python3 /volume1/docker/ariadne-article-preparation-20261004/repair_article_cache_mime.py'
Write-Host "Repair command exited with code $LASTEXITCODE. Leave this window open for verification."
Stop-Transcript
