$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = (py -3 -c "import sys; print(sys.executable.replace('python.exe', 'pythonw.exe'))").Trim()
$tray = Join-Path $root 'tray.py'
$taskName = 'Ariadne Local Control Plane'
$userId = "$env:USERDOMAIN\$env:USERNAME"

$action = New-ScheduledTaskAction -Execute $python -Argument "`"$tray`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $userId
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Write-Output "Installed: $taskName"
Write-Output "Address: http://127.0.0.1:8765"
