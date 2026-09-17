# Регистрация автостарта profi-worker в Task Scheduler (запуск от текущего
# пользователя при входе в систему; schtasks /SC ONLOGON требует админа,
# поэтому через Register-ScheduledTask — для своего пользователя elevation
# не нужен).
#   powershell -File scripts\ops\register-autostart.ps1            # создать
#   powershell -File scripts\ops\register-autostart.ps1 -Remove    # удалить
param([switch]$Remove)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$taskName = "ProfiWorkerAutostart"

if ($Remove) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "задача $taskName удалена"
    exit 0
}

$script = Join-Path $repo "scripts\ops\autostart-win.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$script`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -StartWhenAvailable
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "задача $taskName создана: при входе в Windows поднимутся все аккаунты"
Write-Host "проверка: Get-ScheduledTask -TaskName $taskName"
