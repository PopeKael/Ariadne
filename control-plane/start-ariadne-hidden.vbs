Option Explicit

Dim shell, files, scriptPath, powershellPath, command
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")

scriptPath = files.BuildPath(files.GetParentFolderName(WScript.ScriptFullName), "start-ariadne.ps1")
powershellPath = shell.ExpandEnvironmentStrings("%SystemRoot%") & "\System32\WindowsPowerShell\v1.0\powershell.exe"
command = Chr(34) & powershellPath & Chr(34) & " -NoProfile -ExecutionPolicy Bypass -File " & Chr(34) & scriptPath & Chr(34) & " -OpenBrowser"

shell.CurrentDirectory = files.GetParentFolderName(files.GetParentFolderName(scriptPath))
shell.Run command, 0, False

