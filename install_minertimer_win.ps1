#Requires -RunAsAdministrator
#Requires -Version 5.1

<#
.SYNOPSIS
    Installs MinerTimer as a Windows service using NSSM or as a Scheduled Task.

.DESCRIPTION
    Copies minertimer.ps1 to C:\ProgramData\minertimer and registers it as a
    background service. Uses NSSM (Non-Sucking Service Manager) by default.
    Falls back to Windows Task Scheduler with -UseTaskScheduler.

.PARAMETER UseTaskScheduler
    Use Windows Task Scheduler instead of NSSM. No third-party tools required.

.PARAMETER NssmPath
    Path to nssm.exe. If not specified, searches PATH and common locations.

.EXAMPLE
    .\install_minertimer_win.ps1
    .\install_minertimer_win.ps1 -UseTaskScheduler
    .\install_minertimer_win.ps1 -NssmPath C:\tools\nssm.exe
#>

param(
    [switch]$UseTaskScheduler,
    [string]$NssmPath
)

$ErrorActionPreference = "Stop"
$ServiceName = "MinerTimer"
$TaskName = "MinerTimer"
$BaseDir = Join-Path $env:ProgramData "minertimer"
$ScriptDest = Join-Path $BaseDir "minertimer.ps1"
$EnvFile = Join-Path $BaseDir ".env"
$ScriptSource = Join-Path $PSScriptRoot "minertimer.ps1"

# Step 1: Verify source script exists
if (-not (Test-Path $ScriptSource)) {
    Write-Error "minertimer.ps1 not found in $PSScriptRoot. Place it next to this installer."
    exit 1
}

# Step 2: Create directory and copy script
Write-Host "Installing MinerTimer to $BaseDir ..."
New-Item -ItemType Directory -Path $BaseDir -Force | Out-Null
Copy-Item -Path $ScriptSource -Destination $ScriptDest -Force

# Step 3: Create .env if it doesn't exist
if (-not (Test-Path $EnvFile)) {
    Write-Host "Creating default .env file..."
    @(
        "API_TOKEN=",
        "NOTIFICATION_URL=https://minertimer.lackas.net/update",
        "TIME_LIMIT_DEFAULT=1800"
    ) | Set-Content $EnvFile
    # Restrict permissions: SYSTEM and Administrators only
    $acl = Get-Acl $EnvFile
    $acl.SetAccessRuleProtection($true, $false)
    $adminSid = New-Object System.Security.Principal.SecurityIdentifier("S-1-5-32-544")
    $systemSid = New-Object System.Security.Principal.SecurityIdentifier("S-1-5-18")
    $adminRule = New-Object System.Security.AccessControl.FileSystemAccessRule(
        $adminSid, "FullControl", "Allow")
    $systemRule = New-Object System.Security.AccessControl.FileSystemAccessRule(
        $systemSid, "FullControl", "Allow")
    $acl.AddAccessRule($adminRule)
    $acl.AddAccessRule($systemRule)
    Set-Acl $EnvFile $acl
    Write-Host "Edit $EnvFile to set your API_TOKEN and NOTIFICATION_URL."
}

if ($UseTaskScheduler) {
    # ----- Task Scheduler approach -----
    Write-Host "Registering as Scheduled Task..."

    # Remove existing task if present
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

    $action = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"$ScriptDest`""

    $trigger = New-ScheduledTaskTrigger -AtStartup

    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -RestartCount 9999 `
        -ExecutionTimeLimit (New-TimeSpan -Days 365)

    $principal = New-ScheduledTaskPrincipal `
        -UserId "SYSTEM" `
        -LogonType ServiceAccount `
        -RunLevel Highest

    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Force | Out-Null

    # Start the task now
    Start-ScheduledTask -TaskName $TaskName

    Write-Host ""
    Write-Host "MinerTimer installed as Scheduled Task."
    Write-Host "Verify with: Get-ScheduledTask -TaskName $TaskName"
} else {
    # ----- NSSM approach -----

    # Find NSSM
    if (-not $NssmPath) {
        $NssmPath = (Get-Command nssm -ErrorAction SilentlyContinue).Source
    }
    if (-not $NssmPath) {
        # Check common locations
        $commonPaths = @(
            "C:\tools\nssm.exe",
            "C:\nssm\nssm.exe",
            (Join-Path $BaseDir "nssm.exe"),
            (Join-Path $PSScriptRoot "nssm.exe")
        )
        foreach ($p in $commonPaths) {
            if (Test-Path $p) { $NssmPath = $p; break }
        }
    }

    if (-not $NssmPath -or -not (Test-Path $NssmPath)) {
        Write-Host ""
        Write-Host "NSSM not found. Options:"
        Write-Host "  1. Download NSSM from https://nssm.cc/release/nssm-2.24.zip"
        Write-Host "     Extract nssm.exe and run: .\install_minertimer_win.ps1 -NssmPath C:\path\to\nssm.exe"
        Write-Host "  2. Use Task Scheduler instead (no download needed):"
        Write-Host "     .\install_minertimer_win.ps1 -UseTaskScheduler"
        exit 1
    }

    Write-Host "Using NSSM at $NssmPath"

    # Remove existing service if present
    & $NssmPath stop $ServiceName 2>$null
    & $NssmPath remove $ServiceName confirm 2>$null

    # Install service
    & $NssmPath install $ServiceName "powershell.exe" `
        "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"$ScriptDest`""

    # Configure service
    & $NssmPath set $ServiceName DisplayName "MinerTimer - Minecraft Playtime Control"
    & $NssmPath set $ServiceName Description "Monitors and limits Minecraft playtime"
    & $NssmPath set $ServiceName Start SERVICE_AUTO_START
    & $NssmPath set $ServiceName AppStdout (Join-Path $BaseDir "service.log")
    & $NssmPath set $ServiceName AppStderr (Join-Path $BaseDir "service.log")
    & $NssmPath set $ServiceName AppRotateFiles 1
    & $NssmPath set $ServiceName AppRotateBytes 1048576
    & $NssmPath set $ServiceName AppExit Default Restart
    & $NssmPath set $ServiceName AppRestartDelay 5000

    # Start service
    & $NssmPath start $ServiceName

    Write-Host ""
    Write-Host "MinerTimer installed as Windows Service."
    Write-Host "Verify with: Get-Service $ServiceName"
    Write-Host "Logs at: $BaseDir\service.log"
}

Write-Host ""
Write-Host "Config file: $EnvFile"
Write-Host "Script location: $ScriptDest"
