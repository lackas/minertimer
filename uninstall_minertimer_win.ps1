#Requires -RunAsAdministrator
#Requires -Version 5.1

<#
.SYNOPSIS
    Uninstalls MinerTimer from Windows.

.DESCRIPTION
    Removes the MinerTimer service (NSSM) or Scheduled Task, and cleans up files.

.PARAMETER KeepData
    Keep the playtime log and .env files.

.PARAMETER NssmPath
    Path to nssm.exe if not in PATH.
#>

param(
    [switch]$KeepData,
    [string]$NssmPath
)

$ServiceName = "MinerTimer"
$TaskName = "MinerTimer"
$BaseDir = Join-Path $env:ProgramData "minertimer"

# Try removing NSSM service
$nssmFound = $false
if (-not $NssmPath) {
    $NssmPath = (Get-Command nssm -ErrorAction SilentlyContinue).Source
}
if (-not $NssmPath) {
    $commonPaths = @(
        "C:\tools\nssm.exe",
        "C:\nssm\nssm.exe",
        (Join-Path $BaseDir "nssm.exe")
    )
    foreach ($p in $commonPaths) {
        if (Test-Path $p) { $NssmPath = $p; break }
    }
}

if ($NssmPath -and (Test-Path $NssmPath)) {
    $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($svc) {
        Write-Host "Stopping and removing NSSM service..."
        & $NssmPath stop $ServiceName 2>$null
        & $NssmPath remove $ServiceName confirm 2>$null
        $nssmFound = $true
    }
}

# Try removing Scheduled Task
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    Write-Host "Removing Scheduled Task..."
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

if (-not $nssmFound -and -not $task) {
    Write-Host "No MinerTimer service or task found."
}

# Remove files
if (Test-Path $BaseDir) {
    if ($KeepData) {
        Write-Host "Removing script (keeping data)..."
        Remove-Item (Join-Path $BaseDir "minertimer.ps1") -ErrorAction SilentlyContinue
        Remove-Item (Join-Path $BaseDir "service.log") -ErrorAction SilentlyContinue
    } else {
        Write-Host "Removing $BaseDir ..."
        Remove-Item $BaseDir -Recurse -Force
    }
}

Write-Host ""
Write-Host "MinerTimer has been uninstalled."
if (-not $KeepData) {
    Write-Host "All data has been removed."
} else {
    Write-Host "Data files kept in $BaseDir"
}
