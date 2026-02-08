#Requires -RunAsAdministrator
#Requires -Version 5.1

# MinerTimer Windows Installer
# Downloads and installs MinerTimer as a Scheduled Task.

$ErrorActionPreference = "Stop"
$TaskName = "MinerTimer"
$BaseDir = Join-Path $env:ProgramData "minertimer"
$ScriptDest = Join-Path $BaseDir "minertimer.ps1"
$EnvFile = Join-Path $BaseDir ".env"

Write-Host "Installing MinerTimer to $BaseDir ..."
New-Item -ItemType Directory -Path $BaseDir -Force | Out-Null

# Write minertimer.ps1
$scriptContent = @'
__MINERTIMER_CONTENT__
'@
Set-Content -Path $ScriptDest -Value $scriptContent -Encoding UTF8

# Write .env
$envContent = @"
API_TOKEN=__API_TOKEN__
NOTIFICATION_URL=__NOTIFICATION_URL__
TIME_LIMIT_DEFAULT=1800
"@
if (-not (Test-Path $EnvFile)) {
    Set-Content -Path $EnvFile -Value $envContent -Encoding UTF8
    # Restrict permissions
    $acl = Get-Acl $EnvFile
    $acl.SetAccessRuleProtection($true, $false)
    $adminRule = New-Object System.Security.AccessControl.FileSystemAccessRule(
        "BUILTIN\Administrators", "FullControl", "Allow")
    $systemRule = New-Object System.Security.AccessControl.FileSystemAccessRule(
        "NT AUTHORITY\SYSTEM", "FullControl", "Allow")
    $acl.AddAccessRule($adminRule)
    $acl.AddAccessRule($systemRule)
    Set-Acl $EnvFile $acl
}

# Remove existing task
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

# Create Scheduled Task
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

Start-ScheduledTask -TaskName $TaskName

Write-Host ""
Write-Host "MinerTimer installed successfully."
Write-Host "Config: $EnvFile"
Write-Host "Verify: Get-ScheduledTask -TaskName $TaskName"
