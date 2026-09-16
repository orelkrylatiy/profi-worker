# Stop Windows profi-worker supervisors/workers. Browser processes are intentionally left alive.
# Usage: powershell -File scripts\stop-win.ps1                # all accounts
#        powershell -File scripts\stop-win.ps1 -Account info  # one account
param([string]$Account)

$repo = Split-Path -Parent $PSScriptRoot

function Test-AccountToken([string]$CommandLine, [string]$Flag) {
    if (-not $Account) { return $true }
    if (-not $CommandLine) { return $false }
    $pattern = [regex]::Escape($Flag) + '\s+"?' + [regex]::Escape($Account) + '"?(?:\s|$)'
    return [regex]::IsMatch($CommandLine, $pattern, [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
}

function Test-Supervisor([string]$CommandLine) {
    if (-not $CommandLine) { return $false }
    if ($CommandLine.IndexOf("supervise-win.ps1", [System.StringComparison]::OrdinalIgnoreCase) -lt 0) {
        return $false
    }
    return Test-AccountToken $CommandLine "-Account"
}

function Test-Child([string]$CommandLine) {
    if (-not $CommandLine) { return $false }

    if ($CommandLine.IndexOf("run-worker-win.ps1", [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -and
        (Test-AccountToken $CommandLine "-Account")) { return $true }
    if ($CommandLine.IndexOf("run-autopilot-win.ps1", [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -and
        (Test-AccountToken $CommandLine "-Account")) { return $true }
    if ($Account) {
        if ($CommandLine.IndexOf("profi-worker-$Account.ps1", [System.StringComparison]::OrdinalIgnoreCase) -ge 0) { return $true }
        if ($CommandLine.IndexOf("profi-loop-$Account.ps1", [System.StringComparison]::OrdinalIgnoreCase) -ge 0) { return $true }
        if ($CommandLine.IndexOf("profi.main", [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -and
            (Test-AccountToken $CommandLine "--rhythm-tag")) { return $true }
        return $false
    }

    # All-account stop also cleans legacy temp wrappers and old untagged Python.
    if ($CommandLine.IndexOf("profi-worker-", [System.StringComparison]::OrdinalIgnoreCase) -ge 0) { return $true }
    if ($CommandLine.IndexOf("profi-loop-", [System.StringComparison]::OrdinalIgnoreCase) -ge 0) { return $true }
    if ($CommandLine.IndexOf("profi-autopilot-loop", [System.StringComparison]::OrdinalIgnoreCase) -ge 0) { return $true }
    if ($CommandLine.IndexOf("profi.main", [System.StringComparison]::OrdinalIgnoreCase) -ge 0) { return $true }
    if ($CommandLine.IndexOf(" -m profi", [System.StringComparison]::OrdinalIgnoreCase) -ge 0) { return $true }
    return $false
}

$processes = @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe' OR Name='powershell.exe'" -ErrorAction SilentlyContinue)
$supervisors = @($processes | Where-Object { Test-Supervisor ([string]$_.CommandLine) })
foreach ($proc in $supervisors) {
    Write-Host "stop supervisor pid=$($proc.ProcessId)"
    Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
}

# Stop the owner first so it cannot respawn a worker while cleanup is running.
if ($supervisors.Count -gt 0) {
    Start-Sleep -Seconds 1
}

$processes = @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe' OR Name='powershell.exe'" -ErrorAction SilentlyContinue)
$children = @($processes | Where-Object { Test-Child ([string]$_.CommandLine) })
foreach ($proc in $children) {
    Write-Host "stop child pid=$($proc.ProcessId)"
    Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
}

if ($Account) {
    Remove-Item (Join-Path $repo "data\$Account.autopilot.lock") -ErrorAction SilentlyContinue
} else {
    Remove-Item (Join-Path $repo "data\autopilot.lock") -ErrorAction SilentlyContinue
    Remove-Item (Join-Path $repo "data\*.autopilot.lock") -ErrorAction SilentlyContinue
}

if ($Account) {
    Write-Host "[$Account] stopped; browser left running"
} else {
    Write-Host "all Windows profi-worker processes stopped; browsers left running"
}
