param(
    [string]$TaskName = "GestioStock Django Server",
    [string]$ProjectPath = "C:\Users\DELL\Documents\GitHub\gestio_stock",
    [string]$User = "$env:USERNAME"
)

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-WindowStyle Hidden -File \"$ProjectPath\scripts\start_django_windows.ps1\""
$trigger = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId $User -RunLevel Limited

try {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Force
    Write-Host "Scheduled task '$TaskName' created successfully."
} catch {
    Write-Error "Failed to create scheduled task: $_"
}
