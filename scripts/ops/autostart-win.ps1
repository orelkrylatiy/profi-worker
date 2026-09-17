# Автостарт воркеров после входа в Windows (инцидент 16.09: 3 ребута —
# 16 ч простоя, отклики и чаты дня потеряны).
# Поднимает supervisor на каждый accounts/<акк>.env (start-win идемпотентен:
# живой supervisor не дублируется). Chrome поднимает сам supervisor.
# Регистрация в Task Scheduler: scripts\ops\register-autostart.ps1
$ErrorActionPreference = "Continue"
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$log = Join-Path $repo "logs\autostart.log"
New-Item -ItemType Directory -Force -Path (Join-Path $repo "logs") | Out-Null

function Write-Log([string]$msg) {
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Add-Content -Path $log -Value $line
}

# после ребута сеть/прокси могут подниматься дольше оболочки — даём запас
Start-Sleep -Seconds 30

$accounts = Get-ChildItem (Join-Path $repo "accounts\*.env") -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -notlike "*.example" }
if (-not $accounts) {
    Write-Log "нет accounts/*.env — нечего поднимать"
    exit 0
}
Write-Log ("автостарт: аккаунтов={0} ({1})" -f $accounts.Count, ($accounts.BaseName -join ","))

foreach ($acc in $accounts) {
    try {
        $out = & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repo "scripts\start-win.ps1") -Account $acc.BaseName 2>&1
        Write-Log ("[{0}] {1}" -f $acc.BaseName, ($out -join " | "))
    } catch {
        Write-Log ("[{0}] ОШИБКА: {1}" -f $acc.BaseName, $_.Exception.Message)
    }
    # не стартуем три Chrome одновременно — разносим по времени
    Start-Sleep -Seconds 20
}
Write-Log "автостарт завершён"
